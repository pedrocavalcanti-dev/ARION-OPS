import { createHash, randomBytes } from "node:crypto";
import { spawnSync } from "node:child_process";
import { readFile, readdir, rm, mkdir, writeFile, lstat, rename } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import JavaScriptObfuscator from "javascript-obfuscator";
import esbuild from "esbuild";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoot = path.join(projectRoot, "frontend_src");
const templateRoot = path.join(projectRoot, "arion_geo", "templates");
const staticRoot = path.join(projectRoot, "arion_geo", "static");
const outputRoot = path.join(staticRoot, "arion_build");
const varRoot = path.join(projectRoot, "var");
const manifestPath = path.join(varRoot, "arion-frontend-manifest.json");
const markerName = ".arion-generated";

function assertInside(parent, target) {
  const relative = path.relative(parent, target);
  if (!relative || relative.startsWith(`..${path.sep}`) || relative === ".." || path.isAbsolute(relative)) {
    throw new Error("Build path is outside its allowed directory.");
  }
}

async function exists(target) {
  try {
    await lstat(target);
    return true;
  } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
}

async function walk(directory, extension) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await walk(fullPath, extension));
    else if (entry.isFile() && entry.name.endsWith(extension)) files.push(fullPath);
  }
  return files;
}

async function cleanGeneratedAssets() {
  assertInside(staticRoot, outputRoot);
  const outputExists = await exists(outputRoot);
  const manifestExists = await exists(manifestPath);
  if (!outputExists && !manifestExists) return;
  if (!outputExists || !manifestExists) throw new Error("Refusing to clean an incomplete frontend build.");
  try {
    await readFile(path.join(outputRoot, markerName), "utf8");
    JSON.parse(await readFile(manifestPath, "utf8"));
  } catch {
    throw new Error("Refusing to clean an unmarked or invalid frontend build.");
  }
  await rm(outputRoot, { recursive: true, force: false });
  await rm(manifestPath, { force: false });
}

if (process.argv.includes("--clean")) {
  await cleanGeneratedAssets();
  process.exit(0);
}

const templateFiles = await walk(templateRoot, ".html");
const references = new Set();
const tagPattern = /\{\%\s*(?:arion_js|arion_bundle_js)\s+['"](js\/[A-Za-z0-9_./-]+\.js)['"]\s*%\}/g;
for (const templateFile of templateFiles) {
  const html = await readFile(templateFile, "utf8");
  for (const match of html.matchAll(tagPattern)) references.add(match[1]);
}
if (!references.size) throw new Error("No arion_js template entrypoints were found.");

assertInside(projectRoot, outputRoot);
assertInside(projectRoot, manifestPath);
await mkdir(varRoot, { recursive: true });
const outputExists = await exists(outputRoot);
const manifestExists = await exists(manifestPath);
if (outputExists !== manifestExists) throw new Error("Existing frontend build is incomplete; inspect it before replacing.");
if (outputExists) {
  try {
    await readFile(path.join(outputRoot, markerName), "utf8");
    JSON.parse(await readFile(manifestPath, "utf8"));
  } catch {
    throw new Error("Refusing to replace an unmarked or invalid frontend build.");
  }
}

const suffix = `${process.pid}-${randomBytes(5).toString("hex")}`;
const stagingRoot = path.join(staticRoot, `.arion-build-staging-${suffix}`);
const backupRoot = path.join(staticRoot, `.arion-build-previous-${suffix}`);
const temporaryManifest = path.join(varRoot, `.arion-frontend-manifest-${suffix}.json`);
const previousManifest = path.join(varRoot, `.arion-frontend-manifest-previous-${suffix}.json`);
assertInside(staticRoot, stagingRoot);
assertInside(staticRoot, backupRoot);
assertInside(varRoot, temporaryManifest);
assertInside(varRoot, previousManifest);

const manifest = {};
let movedOldOutput = false;
let movedOldManifest = false;
let installedNewOutput = false;
let installedNewManifest = false;
let committed = false;

try {
  await mkdir(stagingRoot, { recursive: false });
  for (const reference of [...references].sort()) {
    const relativeSource = reference.slice("js/".length);
    const sourcePath = path.resolve(sourceRoot, reference);
    assertInside(sourceRoot, sourcePath);
    const source = await readFile(sourcePath, "utf8");
    const bundle = await esbuild.build({
      stdin: { contents: source, resolveDir: path.dirname(sourcePath), sourcefile: relativeSource, loader: "js" },
      bundle: true,
      write: false,
      outfile: "arion-entry.js",
      platform: "browser",
      format: "iife",
      target: ["es2020"],
      minify: true,
      sourcemap: false,
      legalComments: "none",
      charset: "utf8",
    });
    const minified = bundle.outputFiles[0].text;
    const protectedCode = JavaScriptObfuscator.obfuscate(minified, {
      compact: true,
      identifierNamesGenerator: "hexadecimal",
      renameGlobals: false,
      renameProperties: false,
      stringArray: true,
      stringArrayThreshold: 0.35,
      rotateStringArray: true,
      shuffleStringArray: true,
      splitStrings: true,
      splitStringsChunkLength: 10,
      controlFlowFlattening: false,
      deadCodeInjection: false,
      debugProtection: false,
      selfDefending: false,
      transformObjectKeys: false,
      sourceMap: false,
      target: "browser-no-eval",
    }).getObfuscatedCode();
    const digest = createHash("sha256").update(protectedCode).digest("hex").slice(0, 12);
    const relativeOutput = relativeSource.replace(/\.js$/, `.${digest}.js`);
    const outputPath = path.resolve(stagingRoot, relativeOutput);
    assertInside(stagingRoot, outputPath);
    await mkdir(path.dirname(outputPath), { recursive: true });
    await writeFile(outputPath, protectedCode, { flag: "wx" });
    const syntax = spawnSync(process.execPath, ["--check", outputPath], { encoding: "utf8" });
    if (syntax.status !== 0) throw new Error(`Generated JavaScript failed its syntax check (${reference}).`);
    manifest[reference] = `arion_build/${relativeOutput.split(path.sep).join("/")}`;
  }

  const workerSource = await readFile(path.join(staticRoot, "pwa", "service-worker.js"), "utf8");
  const workerBuild = await esbuild.transform(workerSource, {
    loader: "js",
    minify: true,
    sourcemap: false,
    legalComments: "none",
    target: "es2020",
    charset: "utf8",
  });
  const workerDigest = createHash("sha256").update(workerBuild.code).digest("hex").slice(0, 12);
  const workerRelativeOutput = `pwa/service-worker.${workerDigest}.js`;
  const workerOutputPath = path.resolve(stagingRoot, workerRelativeOutput);
  assertInside(stagingRoot, workerOutputPath);
  await mkdir(path.dirname(workerOutputPath), { recursive: true });
  await writeFile(workerOutputPath, workerBuild.code, { flag: "wx" });
  const workerSyntax = spawnSync(process.execPath, ["--check", workerOutputPath], { encoding: "utf8" });
  if (workerSyntax.status !== 0) throw new Error("Generated Service Worker failed its syntax check.");
  manifest.__service_worker__ = `arion_build/${workerRelativeOutput}`;

  await writeFile(path.join(stagingRoot, markerName), "generated by frontend_src/build.mjs\n", { flag: "wx" });
  await writeFile(temporaryManifest, `${JSON.stringify(manifest, null, 2)}\n`, { encoding: "utf8", flag: "wx" });

  if (outputExists) {
    await rename(outputRoot, backupRoot);
    movedOldOutput = true;
    await rename(manifestPath, previousManifest);
    movedOldManifest = true;
  }
  await rename(stagingRoot, outputRoot);
  installedNewOutput = true;
  await rename(temporaryManifest, manifestPath);
  installedNewManifest = true;
  committed = true;

  if (movedOldOutput) await rm(backupRoot, { recursive: true, force: false });
  if (movedOldManifest) await rm(previousManifest, { force: false });
} catch (error) {
  if (committed) throw new Error("New frontend bundle is active, but generated backup cleanup failed.");
  if (installedNewManifest) await rm(manifestPath, { force: true });
  if (installedNewOutput) await rm(outputRoot, { recursive: true, force: true });
  if (movedOldOutput && await exists(backupRoot)) await rename(backupRoot, outputRoot);
  if (movedOldManifest && await exists(previousManifest)) await rename(previousManifest, manifestPath);
  if (await exists(stagingRoot)) await rm(stagingRoot, { recursive: true, force: true });
  if (await exists(temporaryManifest)) await rm(temporaryManifest, { force: true });
  throw error;
}

console.log(`Production frontend generated: ${references.size} first-party bundles plus a minified Service Worker; source maps disabled.`);
