# Multi-stage build for production
FROM node:20-alpine AS builder

WORKDIR /app

# Install build dependencies
RUN apk add --no-cache python3 make g++

# Copy package files
COPY package.json ./

# Enable corepack + yarn (monorepo uses @repo/design-system workspace dep)
RUN corepack enable && corepack prepare yarn@4.5.0 --activate

# Copy workspace manifests required by yarn workspaces
COPY ../../package.json ../../yarn.lock /workspace-root/
COPY ../../packages/design-system/package.json /workspace-root/packages/design-system/

# Install dependencies (MCP + design-system workspace links)
WORKDIR /workspace-root
RUN yarn workspaces focus open-intelligent-ui-mcp

# Copy MCP source
WORKDIR /app
COPY tsconfig.json ./
COPY src ./src
COPY skills ./skills
# Symlink back design-system dist for the build (focus step copied node_modules)
RUN ln -s /workspace-root/node_modules ./node_modules

# Build
RUN yarn build

# Production image
FROM node:20-alpine

WORKDIR /app

# Install dumb-init for proper signal handling
RUN apk add --no-cache dumb-init

# Copy built application from builder
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/skills ./skills
COPY --from=builder /app/node_modules ./node_modules
COPY package.json ./

# Non-root user
RUN addgroup -g 1001 -S nodejs && \
    adduser -S nodejs -u 1001

USER nodejs

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://localhost:${MCP_PORT:-3100}/health || exit 1

EXPOSE 3100

ENTRYPOINT ["/sbin/dumb-init", "--"]
CMD ["node", "dist/index.js"]
