# Chrome and Word add-ins (sideload)

Two open-source add-ins put the engine where you read and write chemistry. Both talk **directly to your own engine**: one on this machine, a DGX Spark on your network, or a self-hosted deployment on AWS / Azure / GCP. No hosted account, no API key for a single-user engine.

| | Chrome extension | Word add-in |
|---|---|---|
| What it does | Detects SMILES on PubChem, ChEMBL, bioRxiv / medRxiv and arXiv pages; hover or click for a profile; side panel with ADMET, compliance and more | Highlight a SMILES in your manuscript for the same analysis; *Scan Document* profiles every SMILES in the doc |
| Source | [`novomcp-chrome-sideload`](https://github.com/NovoMCP/novomcp-chrome-sideload) | [`novomcp-word-sideload`](https://github.com/NovoMCP/novomcp-word-sideload) |
| Install | Build, then *Load unpacked* in Chrome | Trust a local HTTPS certificate (once per machine), then sideload the manifest |
| One-time setup per user | None | **Yes: a local dev certificate** (see below) |

**Before you start:** have an engine running (see the [Quickstart](quickstart.md), or [DGX Spark](novo-station-dgx-spark.md)) and confirm `curl -s http://localhost:8018/health` returns `{"status":"healthy",...}`.

---

## Chrome extension

### Install

Prerequisites: Node 18+, Chrome 116+.

```bash
git clone https://github.com/NovoMCP/novomcp-chrome-sideload.git
cd novomcp-chrome-sideload
npm install
npm run build
```

In Chrome, open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and select the `dist/` folder.

### Connect

Click the NovoMCP toolbar icon:

- **Engine on this machine:** leave **Engine URL** at `http://localhost:8018`, leave the API key blank, and click **Connect**.
- **Engine on another machine** (a DGX Spark on your network, or a cloud VM): enter its address, e.g. `http://spark-xxxx.local:8018`, and click **Connect**. Chrome asks **once** for permission to reach that host; click **Allow**.

### Use

- Open a compound page, e.g. [PubChem aspirin](https://pubchem.ncbi.nlm.nih.gov/compound/2244). Detected SMILES get a **dashed underline**. In PubChem the canonical one is under *Names and Identifiers → Computed Descriptors → SMILES*.
- **Hover** the underlined text for a preview card, and **click** it to run the profile. **Open in sidebar** gives the full tabs (Profile, ADMET, Compliance, …).
- The extension reacts to SMILES **text**, not to structure images.

!!! note "Reload tabs opened before you connected"
    The extension decorates pages that load *after* it's connected. A tab that was already open when you clicked **Connect** shows no underlines until you reload it.

---

## Word add-in

### Before you start: trust a local HTTPS certificate (once per machine)

Word loads add-ins only over HTTPS, and the sideload serves its taskpane from **your own machine** at `https://localhost:3000`. So **every user needs a locally trusted development certificate, once per computer**:

```bash
git clone https://github.com/NovoMCP/novomcp-word-sideload.git
cd novomcp-word-sideload
npm install
npx office-addin-dev-certs install --days 365
```

- It creates a certificate authority **on your machine only** and trusts it in your keychain (macOS) or certificate store (Windows). You'll be asked for your password. It only vouches for `localhost` on your computer, so it isn't shared and can't be shared. Each user and each machine runs it once.
- **It expires.** The tool's default lifetime is **30 days**; `--days 365` gives a year. Check it with `npx office-addin-dev-certs verify`.
- The Office Store version of the add-in doesn't need this, because it loads from a hosted HTTPS address.

### Run

Use two terminals in the repo:

```bash
# Terminal 1: serves the taskpane on https://localhost:3000. Keep it running.
npm run dev

# Terminal 2: sideloads the manifest into Word and launches Word
npm run start
```

In Word: **Home → NovoMCP → Open NovoMCP**. (If Word didn't launch, open it and use **Insert → My Add-ins → Shared Folder**.)

### Connect

- **Engine on this machine:** keep **Engine URL** at `http://localhost:8018` and click **Connect**.
- **Engine on another machine** (e.g. a DGX Spark on your network): Word blocks plain-`http` calls from its HTTPS taskpane to any host but `localhost`, so route them through the dev server's built-in proxy:

    ```bash
    NOVOMCP_ENGINE_URL=http://spark-xxxx.local:8018 npm run dev
    ```

    and set **Engine URL** to `https://localhost:3000/engine`.

- **Engine behind HTTPS** (e.g. `https://novomcp.your-lab.org`): enter that URL directly.

Then highlight a SMILES in your document, e.g. `CC(=O)OC1=CC=CC=C1C(=O)O`, to see its profile and ADMET.

---

## Remote engines and security

- **Simplest remote setup: an SSH tunnel.** `ssh -L 8018:localhost:8018 <engine-host>` makes a remote engine (a rented Spark, a cloud VM) appear at `http://localhost:8018`, so both add-ins work on their defaults with no proxy or permission prompt.
- **Don't expose `:8018` to the internet.** In the default single-user mode (`NOVO_AUTH=local`) the engine has **no authentication**. Keep it private (SSH tunnel, VPN or private network), or put it behind HTTPS with auth. See [Deploying to the cloud](deploying-to-cloud/README.md).

---

## Troubleshooting

**Word: "The content is blocked because it isn't signed by a valid security certificate"**
The local dev certificate is missing, untrusted or expired. Check the expiry date:
```bash
openssl x509 -in ~/.office-addin-dev-certs/localhost.crt -noout -enddate
```
and replace it (asks for your password):
```bash
npx office-addin-dev-certs uninstall && npx office-addin-dev-certs install --days 365
```
Restart `npm run dev`, which reads the certificate only at startup, then click **Restart** in Word's add-in pane.

**Word: "Sorry, we can't load the add-in… network connectivity"**
`npm run dev` isn't running. The taskpane is served from it, so keep it running while you use the add-in.

**Chrome: connected, but no underlines on the page**
Reload the tab (see the note above), and check that you're on a supported site: PubChem, ChEMBL, bioRxiv / medRxiv, arXiv.

**Either: "Network error" / can't connect**
Check the engine from the same machine: `curl -s <engine-url>/health`. For an engine on another machine, Chrome needs the one-time **Allow** prompt, and Word needs the `/engine` proxy described above.
