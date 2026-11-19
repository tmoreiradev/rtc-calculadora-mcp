# Builds the Receita Federal's official calculator (MIT) from its public repository,
# pinned to one commit. Nothing from that repository is copied into this one.
# Source: https://github.com/ReceitaFederal/calculadora-rtc-regime-regular
FROM eclipse-temurin:21-jdk AS build
ARG RTC_REPO=https://github.com/ReceitaFederal/calculadora-rtc-regime-regular.git
# Release 1.6.1 (database V0059)
ARG RTC_COMMIT=150cccbe414d4a5edea703e4f7a424af5aa222c7
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git init -q . && git remote add origin "$RTC_REPO" \
 && git fetch -q --depth 1 origin "$RTC_COMMIT" && git checkout -q FETCH_HEAD
RUN mkdir -p calculadora/db \
 && ./mvnw -q --batch-mode flyway:migrate -Dflyway.configFiles=./flyway/flyway-pro.conf \
 && ./mvnw -q --batch-mode clean package -DskipTests

FROM eclipse-temurin:21-jre
WORKDIR /app
COPY --from=build /src/target/api-regime-geral.jar /app/api-regime-geral.jar
COPY --from=build /src/calculadora/db/calculadora-pro.db /app/calculadora/db/calculadora-pro.db
COPY --from=build /src/LICENSE /app/LICENSE-calculadora-rtc
EXPOSE 8080
ENTRYPOINT ["java", "-Dspring.profiles.active=offline", "-jar", "/app/api-regime-geral.jar"]
