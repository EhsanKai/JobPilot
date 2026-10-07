# syntax=docker/dockerfile:1
# JobPilot frontend (Next.js, production build, self-contained server).
#
#   docker build -t jobpilot-frontend --build-arg NEXT_PUBLIC_API_URL=... ./frontend
#
# NEXT_PUBLIC_* values are compiled into the browser bundle at build time: they are public
# by definition and must never hold a secret. The frontend needs none.

ARG NODE_IMAGE=node:22-alpine

# ---- dependencies (exactly the lockfile) -------------------------------------------------
FROM ${NODE_IMAGE} AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# ---- build ---------------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS build
WORKDIR /app
ARG NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_OUTPUT_STANDALONE=1 \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ---- runtime: only the standalone server and static assets, as the unprivileged `node` --
FROM ${NODE_IMAGE} AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/login').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]

CMD ["node", "server.js"]
