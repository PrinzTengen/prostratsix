FROM node:20-bookworm-slim

WORKDIR /app

COPY ./package.json /app/
RUN npm install && npm cache clean --force
ENV PATH=/app/node_modules/.bin/:$PATH

COPY . /app

CMD ["npm", "run", "dev"]
