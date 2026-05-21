import { createClient } from "redis";

type RedisClient = ReturnType<typeof createClient>;

const globalForRedis = globalThis as typeof globalThis & {
  sentientRedisPromise?: Promise<RedisClient>;
};

function requireIntegerEnv(name: string, fallback: string): number {
  const raw = process.env[name] ?? fallback;
  const value = Number.parseInt(raw, 10);
  if (!Number.isInteger(value)) {
    throw new Error(`${name} must be an integer`);
  }
  return value;
}

function buildRedisUrl(): string {
  const host = process.env.REDIS_HOST ?? "127.0.0.1";
  const hostPart = host.includes(":") && !host.startsWith("[") ? `[${host}]` : host;
  const port = requireIntegerEnv("REDIS_PORT", "6379");
  const db = requireIntegerEnv("REDIS_DB", "0");
  const username = process.env.REDIS_USERNAME;
  const password = process.env.REDIS_PASSWORD;
  const auth = password
    ? `${username ? encodeURIComponent(username) : ""}:${encodeURIComponent(password)}@`
    : "";

  return `redis://${auth}${hostPart}:${port}/${db}`;
}

export async function getRedis(): Promise<RedisClient> {
  if (!globalForRedis.sentientRedisPromise) {
    const connectTimeout = requireIntegerEnv("REDIS_CONNECT_TIMEOUT", "5000");
    const client = createClient({
      url: buildRedisUrl(),
      socket: {
        connectTimeout,
      },
    });

    client.on("error", (error) => {
      console.error("Redis client error:", error);
    });

    globalForRedis.sentientRedisPromise = client.connect().then(() => client).catch((error) => {
      globalForRedis.sentientRedisPromise = undefined;
      throw error;
    });
  }

  return globalForRedis.sentientRedisPromise;
}
