#!/bin/bash
# exam-start.sh — Start all AWS resources before exam day.
#
# Run from your LAPTOP (requires AWS CLI configured + key file).
# Starts RDS → waits → starts EC2 → waits → launches Docker stack + iris server.
#
# Usage:
#   ./aws/exam-start.sh
#
# Edit the CONFIG section below with your actual AWS resource IDs.

set -euo pipefail

# ════════════════════════════════════════════════════════════════════════════
# CONFIG — fill these in once, then leave them
# ════════════════════════════════════════════════════════════════════════════
EC2_INSTANCE_ID="i-xxxxxxxxxxxxxxxxx"
RDS_INSTANCE_ID="exam-biometrics-db"
EC2_KEY_FILE="$HOME/.ssh/exam-biometrics.pem"
EC2_USER="ubuntu"
APP_DIR="/home/ubuntu/exam_biometrics_full/exam_biometrics_server"
AWS_REGION="ap-south-1"
# ════════════════════════════════════════════════════════════════════════════

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${GREEN}[✓]${NC} $*"; }
waiting() { echo -e "${YELLOW}[…]${NC} $*"; }
die()     { echo -e "${RED}[✗]${NC} $*"; exit 1; }

# Validate config
[ "$EC2_INSTANCE_ID" = "i-xxxxxxxxxxxxxxxxx" ] && die "Edit exam-start.sh and set EC2_INSTANCE_ID"
[ -f "$EC2_KEY_FILE" ] || die "Key file not found: $EC2_KEY_FILE"
command -v aws &>/dev/null || die "AWS CLI not installed or not in PATH"

echo "════════════════════════════════════════════"
echo "  Exam Biometrics — Starting AWS Stack"
echo "  Region:  $AWS_REGION"
echo "  EC2:     $EC2_INSTANCE_ID"
echo "  RDS:     $RDS_INSTANCE_ID"
echo "════════════════════════════════════════════"
echo ""

# ── 1. Start RDS ─────────────────────────────────────────────────────────
waiting "Checking RDS status..."
RDS_STATE=$(aws rds describe-db-instances \
    --db-instance-identifier "$RDS_INSTANCE_ID" \
    --region "$AWS_REGION" \
    --query 'DBInstances[0].DBInstanceStatus' \
    --output text 2>/dev/null) || die "Could not describe RDS instance '$RDS_INSTANCE_ID' — check instance ID and AWS credentials"

if [ "$RDS_STATE" = "available" ]; then
    info "RDS already running"
elif [ "$RDS_STATE" = "stopped" ]; then
    waiting "Starting RDS (usually 3-5 min)..."
    aws rds start-db-instance \
        --db-instance-identifier "$RDS_INSTANCE_ID" \
        --region "$AWS_REGION" > /dev/null
    aws rds wait db-instance-available \
        --db-instance-identifier "$RDS_INSTANCE_ID" \
        --region "$AWS_REGION"
    info "RDS is available"
else
    waiting "RDS is in state '$RDS_STATE' — waiting for it to become available..."
    aws rds wait db-instance-available \
        --db-instance-identifier "$RDS_INSTANCE_ID" \
        --region "$AWS_REGION"
    info "RDS is available"
fi

# ── 2. Start EC2 ─────────────────────────────────────────────────────────
waiting "Checking EC2 status..."
EC2_STATE=$(aws ec2 describe-instances \
    --instance-ids "$EC2_INSTANCE_ID" \
    --region "$AWS_REGION" \
    --query 'Reservations[0].Instances[0].State.Name' \
    --output text)

if [ "$EC2_STATE" = "running" ]; then
    info "EC2 already running"
elif [ "$EC2_STATE" = "stopped" ]; then
    waiting "Starting EC2 instance..."
    aws ec2 start-instances --instance-ids "$EC2_INSTANCE_ID" --region "$AWS_REGION" > /dev/null
    aws ec2 wait instance-running --instance-ids "$EC2_INSTANCE_ID" --region "$AWS_REGION"
    info "EC2 is running"
else
    waiting "EC2 is in state '$EC2_STATE' — waiting..."
    aws ec2 wait instance-running --instance-ids "$EC2_INSTANCE_ID" --region "$AWS_REGION"
    info "EC2 is running"
fi

# ── 3. Get EC2 public IP ──────────────────────────────────────────────────
EC2_IP=$(aws ec2 describe-instances \
    --instance-ids "$EC2_INSTANCE_ID" \
    --region "$AWS_REGION" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text)
[ "$EC2_IP" = "None" ] || [ -z "$EC2_IP" ] && die "Could not get EC2 public IP — does it have an Elastic IP?"
info "EC2 public IP: $EC2_IP"

SSH_OPTS="-i $EC2_KEY_FILE -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes"
SSH="ssh $SSH_OPTS $EC2_USER@$EC2_IP"

# ── 4. Wait for SSH ───────────────────────────────────────────────────────
waiting "Waiting for SSH to be ready..."
for attempt in $(seq 1 36); do
    if $SSH "echo ok" &>/dev/null; then
        info "SSH is ready"
        break
    fi
    [ $attempt -eq 36 ] && die "SSH not available after 6 minutes — check security group (port 22)"
    echo "  attempt $attempt/36 — retrying in 10s..."
    sleep 10
done

# ── 5. Verify EFS is mounted ─────────────────────────────────────────────
waiting "Checking EFS mount..."
if $SSH "mountpoint -q /mnt/efs" 2>/dev/null; then
    info "EFS is mounted"
else
    waiting "EFS not mounted — attempting to mount..."
    $SSH "sudo mount -a" || die "EFS mount failed — run setup.sh on the EC2 instance first"
    $SSH "mountpoint -q /mnt/efs" && info "EFS mounted" || die "EFS still not mounted"
fi

# ── 6. Start Docker Compose stack ────────────────────────────────────────
waiting "Starting Docker Compose stack..."
$SSH "cd $APP_DIR && docker compose --env-file .env.aws -f docker-compose.aws.yml up -d --remove-orphans 2>&1 | tail -5"
info "Docker stack started"

# ── 7. Start iris server ──────────────────────────────────────────────────
waiting "Starting iris server instances..."
$SSH "sudo systemctl start 'iris-api@*' 2>/dev/null; sudo nginx -s reload 2>/dev/null || sudo systemctl restart nginx"
info "Iris server started"

# ── 8. Health check (wait for containers to initialise) ───────────────────
waiting "Waiting 40s for containers to fully initialise..."
sleep 40
waiting "Running health checks..."
$SSH "bash $APP_DIR/aws/health-check.sh" && HEALTH_OK=1 || HEALTH_OK=0

# ── Summary ───────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════"
if [ "$HEALTH_OK" -eq 1 ]; then
    info "${BOLD}System is UP${NC}"
else
    echo -e "${YELLOW}[!]${NC} Some health checks failed — review output above"
fi
echo ""
echo "  Elastic IP / URL:  http://$EC2_IP"
echo "  Admin panel:       http://$EC2_IP/"
echo "  Supervisor panel:  http://$EC2_IP/sp/"
echo "  API docs:          http://$EC2_IP/docs"
echo ""
echo "  When exam is done, run:  ./aws/exam-stop.sh"
echo "════════════════════════════════════════════"
