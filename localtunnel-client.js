"use strict";

const localtunnel = require("localtunnel");

const port = Number.parseInt(process.argv[2], 10);
const subdomain = process.argv[3];
const host = process.env.LOCALTUNNEL_HOST || "https://loca.lt";

if (!Number.isInteger(port) || port < 1 || port > 65535) {
    console.error(`Invalid LocalTunnel port: ${process.argv[2] || "(missing)"}`);
    process.exit(2);
}

if (!subdomain) {
    console.error("LocalTunnel subdomain is required");
    process.exit(2);
}

let tunnel;
let shuttingDown = false;

async function shutdown(exitCode) {
    if (shuttingDown) {
        return;
    }

    shuttingDown = true;
    if (tunnel) {
        tunnel.close();
    }
    process.exit(exitCode);
}

process.once("SIGINT", () => shutdown(130));
process.once("SIGTERM", () => shutdown(143));

(async () => {
    tunnel = await localtunnel({ host, port, subdomain });
    console.log(`your url is: ${tunnel.url}`);

    tunnel.on("error", (error) => {
        console.error("LocalTunnel error:", error);
        shutdown(1);
    });

    tunnel.on("close", () => {
        if (!shuttingDown) {
            console.error("LocalTunnel connection closed");
            process.exit(1);
        }
    });
})().catch((error) => {
    console.error("Unable to establish LocalTunnel:", error);
    process.exit(1);
});
