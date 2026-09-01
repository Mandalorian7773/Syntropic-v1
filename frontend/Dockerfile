# Frontend image. Owner: person 1.
# Two stages: build the SPA, then serve the static output with nginx.
#
# node_modules is COPIED IN, not installed here -- on the air-gapped host there
# is no registry. Run `npm ci` (or unpack the offline bundle) before building.
FROM node:20-slim AS build
WORKDIR /app
COPY package.json ./
COPY node_modules/ ./node_modules/
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
