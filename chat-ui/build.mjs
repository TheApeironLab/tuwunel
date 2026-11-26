// Use the same installed private UI package and toolchain as Vasi.
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { build } from 'vite';
import tailwind from '@tailwindcss/vite';

const root = fileURLToPath(new URL('./', import.meta.url));
const out = new URL('../charts/tuwunel/files/element/', import.meta.url);
const pkg = JSON.parse(await readFile(new URL('./node_modules/@apeiron/ui/package.json', import.meta.url)));
if (pkg.version !== '0.1.105') throw new Error('Expected @apeiron/ui 0.1.105');
const base = { root, configFile: false, logLevel: 'error', define: { 'process.env.NODE_ENV': '"production"' } };
async function bundle(entry) {
    const result = await build({ ...base, build: {
        write: false, minify: true, license: entry === 'navigation.jsx' ? { fileName: 'licenses.txt' } : false, lib: { entry: root + entry, formats: ['es'], fileName: 'module' },
        rolldownOptions: { output: { codeSplitting: false, minify: true } }
    } });
    const output = (Array.isArray(result) ? result[0] : result).output;
    const componentStyles = output.find(asset => asset.type === 'asset' && asset.fileName.endsWith('.css'));
    if (!componentStyles) throw new Error('Missing LoginPage Beams styles');
    await writeFile(new URL('beams.css', out), componentStyles.source);
    const licenses = output.find(asset => asset.fileName === 'licenses.txt');
    if (licenses) await writeFile(new URL('licenses.txt', out), String(licenses.source).replaceAll('\r\n', '\n'));
    return output.find(chunk => chunk.type === 'chunk').code;
}
await writeFile(new URL('navigation.js', out), (await bundle('navigation.jsx')).replace(/^[ \t]+$/gm, ''));
const css = await build({ ...base, plugins: [tailwind()], build: {
    write: false, cssMinify: true, rolldownOptions: { input: root + 'components.css' }
} });
const styles = css.output.find(chunk => chunk.type === 'asset' && chunk.fileName.endsWith('.css')).source;
// Isolate Preflight and utility classes from the upstream Matrix client.
// Radix renders the mobile Sidebar and its backdrop in a portal under body.
const scope = '(.ap-scope, body:has(> [role=dialog] [data-sidebar-trigger])) to (:scope:is(body) > :not([role=dialog], [data-state]))';
await writeFile(new URL('components.css', out),
    '@scope ' + scope + '{' + styles.replaceAll(':root', ':scope').replaceAll('.dark{', ':is(.dark, body.cpd-theme-dark:scope){') + '}');
