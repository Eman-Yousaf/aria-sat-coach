import { createInterface } from "readline";

const base = "file:///C:/Users/T14/AppData/Roaming/npm/node_modules/openclaw/dist/";

const [
  { n: runMessageAction },
  { t: createDefaultDeps },
  { t: createOutboundSendDeps },
  { n: defaultRuntime, r: writeRuntimeJson },
  { i: getRuntimeConfig },
  { c: resolveDefaultAgentId },
  { t: resolveCommandConfigWithSecrets },
  { d: getScopedChannelsCommandSecretTargets },
  { t: resolveMessageSecretScope },
  { i: GATEWAY_CLIENT_NAMES, r: GATEWAY_CLIENT_MODES },
  { t: formatMessageCliText },
] = await Promise.all([
  import(base + "message-action-runner-jQYM2Orj.js"),
  import(base + "deps-DcaRKxrN.js"),
  import(base + "outbound-send-deps-CmzyscBN.js"),
  import(base + "runtime-B4lgFmsS.js"),
  import(base + "io-CwmOK6NP.js"),
  import(base + "agent-scope-config-ChfGvhEr.js"),
  import(base + "command-config-resolution-9oGtCrE0.js"),
  import(base + "command-secret-targets-B2DBte9W.js"),
  import(base + "message-secret-scope-5IkxbpdZ.js"),
  import(base + "client-info-CcqJJIan.js"),
  import(base + "message-format-CozS46QB.js"),
]);

console.error("[worker] openclaw modules loaded, sending ready");

const rl = createInterface({ input: process.stdin, terminal: false });

for await (const line of rl) {
  let cmd;
  try { cmd = JSON.parse(line); } catch {
    process.stdout.write(JSON.stringify({ ok: false, error: "invalid JSON" }) + "\n"); continue;
  }

  const { phone, message } = cmd;
  if (!phone || message === undefined) {
    process.stdout.write(JSON.stringify({ ok: false, error: "missing phone/message" }) + "\n"); continue;
  }

  try {
    const phoneDigits = phone.replace(/\D/g, "");
    const runtime = defaultRuntime;
    const rawCfg = getRuntimeConfig(runtime, { verbose: false });
    const scope = resolveMessageSecretScope({ channel: "whatsapp", target: "+" + phoneDigits });
    const scopedTargets = getScopedChannelsCommandSecretTargets({ config: rawCfg, channel: "whatsapp", accountId: undefined });
    const { effectiveConfig: cfg } = await resolveCommandConfigWithSecrets({
      config: rawCfg, commandName: "message",
      targetIds: scopedTargets.targetIds,
      ...(scopedTargets.allowedPaths ? { allowedPaths: scopedTargets.allowedPaths } : {}),
      runtime, autoEnable: true,
    });

    const deps = createDefaultDeps(runtime);
    const outboundDeps = createOutboundSendDeps(deps);

    const opts = {
      channel: "whatsapp",
      target: "+" + phoneDigits,
      message: message,
      json: true,
      dryRun: false,
      verbose: false,
      silent: false,
      pin: false,
      forceDocument: false,
      gifPlayback: false,
    };

    const result = await runMessageAction({
      cfg,
      action: "send",
      params: opts,
      deps: outboundDeps,
      agentId: resolveDefaultAgentId(cfg),
      senderIsOwner: true,
      gateway: { clientName: GATEWAY_CLIENT_NAMES.CLI, mode: GATEWAY_CLIENT_MODES.CLI },
    });

    process.stdout.write(JSON.stringify({ ok: true, output: result }) + "\n");
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: String(e).slice(0, 1000) }) + "\n");
  }
}
