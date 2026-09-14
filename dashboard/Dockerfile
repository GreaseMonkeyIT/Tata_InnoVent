# Build the static export, then serve it with nginx (which also proxies /api/ to the api gateway).
# 2E: the conf is now an envsubst TEMPLATE (official image mechanism) so the operator token from
# the visr-auth Secret reaches the role->token map without ever being baked into the image.
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:alpine
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/out /usr/share/nginx/html
EXPOSE 80 443
