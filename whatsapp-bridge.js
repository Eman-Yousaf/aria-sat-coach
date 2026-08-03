const { Client, LocalAuth } = require('whatsapp-web.js');
const express = require('express');
const qrcode = require('qrcode-terminal');

const PORT = parseInt(process.env.BRIDGE_PORT || '18790');
const WEBHOOK_URL = process.env.WEBHOOK_URL || 'http://127.0.0.1:18791/webhook';

const app = express();
app.use(express.json());

let bridgeReady = false;

const client = new Client({
    authStrategy: new LocalAuth(),
    puppeteer: {
        headless: true,
        executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    }
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
