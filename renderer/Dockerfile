# Slim HT deck renderer: the committed bundle in dist/ (see dist/BUILD_INFO)
# and the ht-media theme chain, nothing else.
FROM node:20-slim
WORKDIR /app
COPY dist/ ./
ENV NODE_ENV=production
USER node
EXPOSE 8080
CMD ["node", "server.mjs"]
