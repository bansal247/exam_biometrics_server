#!/bin/bash
# exam-stop.sh — Gracefully stop all AWS resources after exam day.
#
# Sequence: stop Docker → stop iris → RDS snapshot → stop EC2 → stop RDS.
# The RDS snapshot completes in the background (usually 5-10 min) after this
# script exits — you do NOT need to wait for it.
#
# IMPORTANT: AWS auto-restarts a stopped RDS after 7 days. If your gap between
# exams is longer than 7 days, re-run this script after the auto-restart to
# stop it again (or set up the Lambda workaround — see README).
#
# Usage (from your laptop):
#   ./aws/exam-stop.sh

set -euo pipefail

# ════════════════════════════════════════════════════════════════════════════
# CONFIG — must match exam-start.sh
# ════════════════════════════════════════════════════════════════════════════
EC2_INSTANCE_ID="i-xxxxxxxxxxxxxxxxx"
RDS_INSTANCE_ID="exam-biometrics-db"
EC2_KEY_FILE="$HOME/.ssh/exam-biometrics.pem"
EC2_USER="ubuntu"
APP_DIR="/home/ubuntu/exam_biometrics_full/exam_biometrics_server"
AWS_REGION="ap-south-1"
# ════════════════════════════════════════════════════════════════════════════

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${GREEN}[✓]${NC} $*"; }
waiting() { echo -e "${YELLOW}[…]${NC} $*"; }
warn()    { echo -e "${YELLOW}[!]${NC} $*"; }
die()     { echo -e "${RED}[✗]${NC} $*"; exit 1; }

[ "$EC2_INSTANCE_ID" = "i-xxxxxxxxxxxxxxxxx" ] && die "Edit exam-stop.sh and set EC2_INSTANCE_ID"
command -v aws &>/dev/null || die "AWS CLI not installed"

SNAPSHOT_ID="exam-biometrics-$(date +%Y%m%d-%H%M)"

echo "════════════════════════════════════════════"
echo "  Exam Biometrics — Stopping AWS Stack"
echo "  Snapshot will be: $SNAPSHOT_ID"
echo "════════════════════════════════════════════"
echo ""

# ── 1. Get EC2 state ─────────────────────────────────────────────────────
EC2_STATE=$(aws ec2 describe-instances \
    --instance-ids "$EC2_INSTANCE_ID" \
    --region "$AWS_REGION" \
    --query 'Reservations[0].Instances[0].State.Name' \
    --output text)

# ── 2. Graceful Docker shutdown (only if EC2 is running) ─────────────────
if [ "$EC2_STATE" = "running" ]; then
    EC2_IP=$(aws ec2 describe-instances \
        --instance-ids "$EC2_INSTANCE_ID" \
        --region "$AWS_REGION" \
        --query 'Reservations[0].Instances[0].PublicIpAddress' \
        --output text)

    SSH_OPTS="-i $EC2_KEY_FILE -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes"
    SSH="ssh $SSH_OPTS $EC2_USER@$EC2_IP"

    if $SSH "echo ok" &>/dev/null; then
        waiting "Stopping Docker Compose stack gracefully..."
        $SSH "cd $APP_DIR && docker compose --env-file .env.aws -f docker-compose.aws.yml down" \
            && info "Docker stack stopped" \
            || warn "Docker stop had errors (proceeding anyway)"

        waiting "Stopping iris server..."
        $SSH "sudo systemctl stop 'iris-api@*' 2>/dev/null || true" \
            && info "Iris server stopped"
    else
        warn "SSH not reachable — skipping graceful Docker shutdown"
    fi
else
    info "EC2 is already $EC2_STATE — skipping Docker shutdown"
fi

# ── 3. RDS snapshot ───────────────────────────────────────────────────────
RDS_STATE=$(aws rds describe-db-instances \
    --db-instance-identifier "$RDS_INSTANCE_ID" \
    --region "$AWS_REGION" \
    --query 'DBInstances[0].DBInstanceStatus' \
    --output text 2>/dev/null || echo "unknown")

if [ "$RDS_STATE" = "available" ]; then
    waiting "Creating RDS snapshot: $SNAPSHOT_ID ..."
    aws rds create-db-snapshot \
        --db-instance-identifier "$RDS_INSTANCE_ID" \
        --db-snapshot-identifier "$SNAPSHOT_ID" \
        --region "$AWS_REGION" > /dev/null
    info "Snapshot initiated: $SNAPSHOT_ID (completes in background, ~5-10 min)"
else
    warn "RDS state is '$RDS_STATE' — skipping snapshot"
fi

# ── 4. Stop EC2 ───────────────────────────────────────────────────────────
if [ "$EC2_STATE" = "running" ]; then
    waiting "Stopping EC2 instance..."
    aws ec2 stop-instances --instance-ids "$EC2_INSTANCE_ID" --region "$AWS_REGION" > /dev/null
    info "EC2 stop initiated (instance data persists on EBS)"
else
    info "EC2 already $EC2_STATE"
fi

# ── 5. Stop RDS ───────────────────────────────────────────────────────────
if [ "$RDS_STATE" = "available" ]; then
    waiting "Stopping RDS instance..."
    aws rds stop-db-instance \
        --db-instance-identifier "$RDS_INSTANCE_ID" \
        --region "$AWS_REGION" > /dev/null
    info "RDS stop initiated (data persists, storage charges continue)"
else
    info "RDS already $RDS_STATE"
fi

# ── Summary ───────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════"
info "All resources stopping"
echo ""
echo "  RDS snapshot:  $SNAPSHOT_ID"
echo "  EC2 state:     stopping → stopped"
echo "  RDS state:     stopping → stopped"
echo ""
echo "  Costs while idle:"
echo "    EFS storage:  ~\$0.30/GB/month (always)"
echo "    RDS storage:  ~\$0.115/GB/month (always)"
echo "    EC2 EBS root: ~\$0.10/GB/month (always)"
echo "    EC2 compute:  \$0 (stopped)"
echo "    RDS compute:  \$0 (stopped)"
echo ""
warn "AWS auto-restarts stopped RDS after 7 days."
echo "  If you see unexpected RDS charges, just re-run this script."
echo "════════════════════════════════════════════"
