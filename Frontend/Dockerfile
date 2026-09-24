# Frontend Zaynb — Next.js (standalone)
# Build-time :
#   BACKEND_INTERNAL_URL        URL du backend vue par Next.js (proxy /api/v1, /health)
#   NEXT_PUBLIC_LOCAL_DATA_ROOT racine des données (exemples de chemins affichés)
#   NEXT_PUBLIC_API_URL         optionnel — appel direct du backend par le navigateur

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN mkdir -p public

ARG NEXT_PUBLIC_API_URL=
ARG BACKEND_INTERNAL_URL=http://backend:8000
ARG NEXT_PUBLIC_LOCAL_DATA_ROOT=/data/zaynb
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    BACKEND_INTERNAL_URL=$BACKEND_INTERNAL_URL \
    NEXT_PUBLIC_LOCAL_DATA_ROOT=$NEXT_PUBLIC_LOCAL_DATA_ROOT
ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup -g 1001 -S nodejs && adduser -u 1001 -S nextjs -G nodejs

RUN mkdir -p ./public
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD wget -qO- http://127.0.0.1:3000/ || exit 1

CMD ["node", "server.js"]
