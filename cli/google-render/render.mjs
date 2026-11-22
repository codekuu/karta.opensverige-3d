// Renders one cell from Google Photorealistic 3D Tiles through Cesium ion.
//
//   node render.mjs --camera <cell>.camera.json --out <cell>.google.png
//
// rutor render writes the camera file and calls this. The Cesium ion token comes from
// CESIUM_ION_TOKEN in the environment and goes only to the page, never into a URL we log.
// Exit codes follow rutor: 3 for the environment or the token, 4 for the network or the tiles.
// The last stdout line is a JSON summary.

import { createServer } from "node:http";
import { createRequire } from "node:module";
import { existsSync } from "node:fs";
import { readFile, writeFile } from "node:fs/promises";
import { dirname, extname, join, relative, resolve, isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".wasm": "application/wasm",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".ktx2": "image/ktx2",
  ".xml": "application/xml",
  ".txt": "text/plain; charset=utf-8",
};

class Failure extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

function arg(flag) {
  const index = process.argv.indexOf(flag);
  if (index === -1 || index + 1 >= process.argv.length) throw new Failure(3, `missing ${flag}`);
  return process.argv[index + 1];
}

function inside(root, target) {
  const rel = relative(root, target);
  return rel !== "" && !rel.startsWith("..") && !isAbsolute(rel);
}

function serve(cesiumBuild) {
  const pageRoot = join(here, "page");
  const server = createServer(async (request, response) => {
    const url = new URL(request.url || "/", "http://127.0.0.1");
    let file = null;
    if (url.pathname === "/favicon.ico") {
      response.writeHead(204);
      response.end();
      return;
    }
    if (url.pathname === "/" || url.pathname === "/index.html") {
      file = join(pageRoot, "index.html");
    } else if (url.pathname.startsWith("/page/")) {
      file = resolve(pageRoot, url.pathname.slice("/page/".length));
      if (!inside(pageRoot, file)) file = null;
    } else if (url.pathname.startsWith("/cesium/")) {
      file = resolve(cesiumBuild, decodeURIComponent(url.pathname.slice("/cesium/".length)));
      if (!inside(cesiumBuild, file)) file = null;
    }
    if (!file || !existsSync(file)) {
      response.writeHead(404);
      response.end();
      return;
    }
    try {
      const body = await readFile(file);
      response.writeHead(200, { "Content-Type": MIME[extname(file).toLowerCase()] || "application/octet-stream" });
      response.end(body);
    } catch {
      response.writeHead(500);
      response.end();
    }
  });
  return new Promise((done, fail) => {
    server.once("error", fail);
    server.listen(0, "127.0.0.1", () => done({ server, port: server.address().port }));
  });
}

function chromeArgs(mode) {
  const shared = ["--hide-scrollbars", "--mute-audio", "--enable-webgl", "--ignore-gpu-blocklist", "--disable-dev-shm-usage"];
  if (mode === "swiftshader") return [...shared, "--use-angle=swiftshader", "--enable-unsafe-swiftshader"];
  if (process.platform === "win32") return [...shared, "--use-angle=d3d11", "--enable-gpu"];
  if (process.platform === "darwin") return [...shared, "--use-angle=metal", "--enable-gpu"];
  return [...shared, "--use-angle=gl-egl", "--enable-gpu", "--disable-gpu-sandbox"];
}

async function renderOnce(puppeteer, view, port, token, mode) {
  const browser = await puppeteer.launch({ headless: true, protocolTimeout: 600000, args: chromeArgs(mode) });
  const problems = [];
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(600000);
    page.on("pageerror", (error) => problems.push(error.message));
    page.on("response", (response) => {
      // The query string carries the token, so only the path is kept.
      if (response.status() >= 400) problems.push(`${response.status()} ${response.url().split("?")[0]}`);
    });
    await page.setViewport({ width: view.width, height: view.height, deviceScaleFactor: 1 });
    await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: "domcontentloaded" });
    await page.waitForFunction("window.Cesium && window.renderCell", { timeout: 60000 });
    const gl = await page.evaluate(() => {
      const gl2 = document.createElement("canvas").getContext("webgl2");
      if (!gl2) return "none";
      const info = gl2.getExtension("WEBGL_debug_renderer_info");
      return String(info ? gl2.getParameter(info.UNMASKED_RENDERER_WEBGL) : gl2.getParameter(gl2.RENDERER));
    });
    const result = await page.evaluate((config) => window.renderCell(config), { view, token });
    return { result, gl, problems };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const seen = problems.length ? ` (${problems.slice(-3).join("; ")})` : "";
    const auth = /\b40[13]\b|unauthori[sz]ed|invalid token|access token/i.test(message + seen);
    const quota = /\b429\b/.test(message + seen);
    const said = auth
      ? `Cesium ion refused the token${seen}`
      : quota
        ? `the Cesium ion quota is used up for now${seen}`
        : `${message}${seen}`;
    const failure = new Failure(auth ? 3 : 4, said);
    // A used-up quota fails the same way on the software renderer, so it is not tried again.
    failure.retry = !auth && !quota;
    throw failure;
  } finally {
    await browser.close();
  }
}

async function main() {
  const token = (process.env.CESIUM_ION_TOKEN || "").trim();
  if (!token) throw new Failure(3, "CESIUM_ION_TOKEN is not set");
  const view = JSON.parse(await readFile(arg("--camera"), "utf8"));
  const out = arg("--out");
  let puppeteer;
  let cesiumBuild;
  try {
    puppeteer = (await import("puppeteer")).default;
    cesiumBuild = join(dirname(require.resolve("cesium/package.json")), "Build", "Cesium");
  } catch {
    throw new Failure(3, `run npm ci in ${here}`);
  }
  const { server, port } = await serve(cesiumBuild);
  try {
    let rendered;
    try {
      rendered = await renderOnce(puppeteer, view, port, token, "gpu");
    } catch (error) {
      if (error.code === 3 || error.retry === false) throw error;
      console.log(`gpu render failed (${error.message}), trying the software renderer`);
      rendered = await renderOnce(puppeteer, view, port, token, "swiftshader");
    }
    const { result, gl } = rendered;
    if (!result.orthographic) throw new Failure(4, "the camera is not orthographic");
    if (result.width !== view.width || result.height !== view.height) {
      throw new Failure(4, `the canvas is ${result.width}x${result.height}, expected ${view.width}x${view.height}`);
    }
    const comma = result.png.indexOf(",");
    await writeFile(out, Buffer.from(result.png.slice(comma + 1), "base64"));
    console.log(
      JSON.stringify({
        cesium: result.cesium,
        tiles: result.tiles,
        gl,
        heading: result.heading.toFixed(3),
        pitch: result.pitch.toFixed(3),
      }),
    );
  } finally {
    await new Promise((done) => server.close(() => done()));
  }
}

main().catch((error) => {
  console.error(`error: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = error instanceof Failure ? error.code : 3;
});
