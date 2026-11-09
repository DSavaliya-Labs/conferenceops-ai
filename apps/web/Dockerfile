# syntax=docker/dockerfile:1
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npx ng build

# Unprivileged nginx: runs as a non-root user and listens on 8080 (K8S-1).
FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY --from=build /app/dist/web/browser /usr/share/nginx/html
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
# nginx resolves the API names per request, with the resolver from /etc/resolv.conf (the cluster DNS on AKS), so it
# starts before the APIs exist. nginx ignores resolv.conf search domains, so the names are fully qualified.
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 \
    CORE_UPSTREAM=conferenceops-core.conferenceops.svc.cluster.local:80 \
    DISCOVERY_UPSTREAM=conferenceops-discovery.conferenceops.svc.cluster.local:80 \
    DASH0_WEB_ENDPOINT=https://ingress.us-west-2.aws.dash0.com \
    DASH0_BROWSER_TOKEN="" \
    DASH0_DATASET=conferenceops \
    ENVIRONMENT=local \
    SERVICE_VERSION=dev
EXPOSE 8080
