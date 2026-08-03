import { createInterface } from "readline";

const openclawPath = "file:///C:/Users/T14/AppData/Roaming/npm/node_modules/openclaw/openclaw.mjs";
const openclaw = await import(openclawPath);

console.error("openclaw keys:", Object.keys(openclaw));

const rl = createInterface({ input: process.stdin, terminal: false });

for await (const line of rl) {
  let cmd;
  try {
    cmd = JSON.parse(line);
  } catch {
    process.stdout.write(JSON.stringify({ ok: false, error: "invalid JSON" }) + "\n");
    continue;
  }

  const { phone, message } = cmd;
  const cleanPhone = phone.replace(/\D/g, "");

  try {
    if (openclaw.sendMessage) {
      const result = await openclaw.sendMessage({
        channel: "whatsapp",
        target: "+" + cleanPhone,
        message: message,
      });
      process.stdout.write(JSON.stringify({ ok: true, output: String(result).slice(0, 1000) }) + "\n");
    } else {
      // fallback: use child_process
      const { execFileSync } = await import("child_process");
      const result = execFileSync(
        process.execPath,
        [
          "C:\\Users\\T14\\AppData\\Roaming\\npm\\node_modules\\openclaw\\openclaw.mjs",
          "message", "send",
          "--channel", "whatsapp",
          "--target", "+" + cleanPhone,
          "-m", message,
          "--json"
        ],
        { env: process.env, encoding: "utf-8", timeout: 120_000 }
      );
      process.stdout.write(JSON.stringify({ ok: true, output: result.slice(0, 1000) }) + "\n");
    }
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: String(e).slice(0, 500) }) + "\n");
  }
}
