// Element Web module API v2. Keep the stylesheet on the same origin as Chat.
export default class ApeironTheme {
    static moduleApiVersion = "^2.2.0";

    async load() {
        const url = new URL("theme.css", import.meta.url);
        url.search = new URL(import.meta.url).search;
        const stylesheet = document.createElement("link");
        stylesheet.rel = "stylesheet";
        stylesheet.href = url.href;
        document.head.append(stylesheet);
    }
}
