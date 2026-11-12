# Stage 1: build the static app. Stage 2: serve it with Caddy, which also forwards /api/*
# to the API and, given a domain name, obtains and renews HTTPS certificates automatically.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM caddy:2.10-alpine
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
