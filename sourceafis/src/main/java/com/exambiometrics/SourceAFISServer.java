package com.exambiometrics;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.machinezoo.sourceafis.FingerprintTemplate;
import com.machinezoo.sourceafis.FingerprintMatcher;
import com.machinezoo.sourceafis.FingerprintImage;
import com.machinezoo.sourceafis.FingerprintImageOptions;
import io.javalin.Javalin;
import io.javalin.http.Context;

import java.util.Base64;
import java.util.logging.Level;
import java.util.logging.Logger;

public class SourceAFISServer {

    private static final Gson gson = new Gson();
    private static final Logger logger = Logger.getLogger(SourceAFISServer.class.getName());

    public static void main(String[] args) {
        Javalin app = Javalin.create().start(8080);

        app.get("/health", ctx -> ctx.json("{\"status\":\"ok\"}"));
        app.post("/extract", SourceAFISServer::handleExtract);
        app.post("/match",   SourceAFISServer::handleMatch);

        logger.info("SourceAFIS service running on port 8080");
    }

    /**
     * POST /extract
     * Body: { "image": "<base64 BMP>" }
     * Response: { "template": "<base64 template>" }
     */
    private static void handleExtract(Context ctx) {
        try {
            JsonObject body = gson.fromJson(ctx.body(), JsonObject.class);
            String imageB64 = body.get("image").getAsString();
            byte[] imageBytes = Base64.getDecoder().decode(imageB64);

            FingerprintImage image = new FingerprintImage(
                imageBytes,
                new FingerprintImageOptions().dpi(500)
            );
            FingerprintTemplate template = new FingerprintTemplate(image);
            byte[] serialized = template.toByteArray();

            JsonObject response = new JsonObject();
            response.addProperty("template", Base64.getEncoder().encodeToString(serialized));
            logger.info("extract: success, template size=" + serialized.length);
            ctx.json(response.toString());

        } catch (Exception e) {
            logger.log(Level.WARNING, "extract failed: " + e.getMessage(), e);
            ctx.status(400);
            JsonObject err = new JsonObject();
            err.addProperty("error", e.getMessage());
            ctx.json(err.toString());
        }
    }

    /**
     * POST /match
     * Body: { "probe": "<base64 template>", "candidate": "<base64 template>" }
     * Response: { "score": 45.2 }
     * NOTE: threshold comparison is done by the Python server using env FINGERPRINT_MATCH_THRESHOLD.
     */
    private static void handleMatch(Context ctx) {
        try {
            JsonObject body = gson.fromJson(ctx.body(), JsonObject.class);
            String probeB64     = body.get("probe").getAsString();
            String candidateB64 = body.get("candidate").getAsString();

            byte[] probeBytes     = Base64.getDecoder().decode(probeB64);
            byte[] candidateBytes = Base64.getDecoder().decode(candidateB64);

            FingerprintTemplate probe     = new FingerprintTemplate(probeBytes);
            FingerprintTemplate candidate = new FingerprintTemplate(candidateBytes);

            FingerprintMatcher matcher = new FingerprintMatcher(probe);
            double score = matcher.match(candidate);

            JsonObject response = new JsonObject();
            response.addProperty("score", score);
            logger.info("match: score=" + score);
            ctx.json(response.toString());

        } catch (Exception e) {
            logger.log(Level.WARNING, "match failed: " + e.getMessage(), e);
            ctx.status(400);
            JsonObject err = new JsonObject();
            err.addProperty("error", e.getMessage());
            ctx.json(err.toString());
        }
    }
}
