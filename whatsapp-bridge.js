const { Client, LocalAuth } = require('whatsapp-web.js');
const express = require('express');
const qrcode = require('qrcode-terminal');

const PORT = parseInt(process.env.BRIDGE_PORT || '18790');
const WEBHOOK_URL = process.env.WEBHOOK_URL || 'http://127.0.0.1:18791/webhook';

const app = express();
app.use(express.json());

let bridgeReady = false;

// Where to find Chrome. This used to be a hardcoded Windows path, which meant
// the bridge started on exactly one laptop and failed everywhere else with an
// error that reads like a WhatsApp problem rather than a missing browser.
//
// Unset, puppeteer uses the Chromium it downloaded during `npm install`, which
// is the case that needs no configuration at all. Set CHROME_PATH to point at a
// system Chrome instead -- worth doing on a slow connection, since it saves
// fetching another browser.
const CHROME_PATH = process.env.CHROME_PATH || undefined;

const puppeteerOptions = {
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
};
if (CHROME_PATH) {
    puppeteerOptions.executablePath = CHROME_PATH;
}

const client = new Client({
    authStrategy: new LocalAuth(),
    puppeteer: puppeteerOptions
});

client.on('qr', qr => {
    console.log('QRCODE:' + qr);
    qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
    bridgeReady = true;
    console.log('BRIDGE_READY');
});

client.on('authenticated', () => {
    console.log('BRIDGE_AUTHENTICATED');
});

client.on('auth_failure', msg => {
    console.error('BRIDGE_AUTH_FAILURE: ' + msg);
});

client.on('disconnected', reason => {
    bridgeReady = false;
    console.error('BRIDGE_DISCONNECTED: ' + reason);
});

function digitsOnly(value) {
    return String(value || '').replace(/\D/g, '');
}

async function resolveSendTarget(to, chatId) {
    if (chatId) {
        return chatId;
    }
    if (!to) {
        return null;
    }
    const numberId = await client.getNumberId(digitsOnly(to));
    return numberId ? numberId._serialized : null;
}

client.on('message', async msg => {
    if (msg.fromMe) return;
    // Ignore group chats — only handle direct messages
    if (msg.from.endsWith('@g.us')) return;

    try {
        const contact = await msg.getContact();
        const phone = digitsOnly(contact.number || contact.id?.user || msg.from.split('@')[0]);
        const payload = {
            from: phone,
            chatId: msg.from,
            name: contact.pushname || contact.name || phone,
            body: msg.body,
            timestamp: String(msg.timestamp)
        };
        const resp = await fetch(WEBHOOK_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            signal: AbortSignal.timeout(30000)
        });
        if (!resp.ok) {
            console.error('webhook returned ' + resp.status);
        }
    } catch (err) {
        console.error('webhook error: ' + err.message);
    }
});

app.post('/send', async (req, res) => {
    const { to, chatId, message } = req.body;
    if (!message || (!to && !chatId)) {
        return res.status(400).json({ error: 'message and (to or chatId) required' });
    }
    if (!bridgeReady) {
        return res.status(503).json({ error: 'bridge not ready yet' });
    }
    try {
        const target = await resolveSendTarget(to, chatId);
        if (!target) {
            return res.status(400).json({ error: 'could not resolve WhatsApp recipient' });
        }
        await client.sendMessage(target, message);
        res.json({ ok: true, target });
    } catch (err) {
        console.error('send error: ' + err.message);
        res.status(500).json({ error: err.message });
    }
});

app.get('/health', (req, res) => {
    res.json({ ok: true, ready: bridgeReady, status: bridgeReady ? 'connected' : 'connecting' });
});

client.initialize();
app.listen(PORT, () => {
    console.log('BRIDGE_LISTENING:' + PORT);
});
