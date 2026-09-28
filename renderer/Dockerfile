# Slim HT deck renderer. Build dist/ first with renderer/build.sh; this image
# contains only that bundle and the ht-media theme chain.
FROM node:20-slim
WORKDIR /app
COPY dist/ ./
ENV NODE_ENV=production
USER node
EXPOSE 8080
CMD ["node", "server.mjs"]
