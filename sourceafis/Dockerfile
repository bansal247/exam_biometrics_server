FROM gradle:8.10-jdk21 AS build

WORKDIR /app
COPY build.gradle settings.gradle ./
COPY src ./src
RUN gradle shadowJar --no-daemon

FROM eclipse-temurin:21-jre-alpine
WORKDIR /app
COPY --from=build /app/build/libs/sourceafis-service-all.jar app.jar
EXPOSE 8080
CMD ["java", "-jar", "app.jar"]
