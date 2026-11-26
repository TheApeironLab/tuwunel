# Chat navigation and sign-in

Element Web v1.12.30 loads the module from the Chart. The native Matrix client owns the sign-in form, SSO redirects, room directory, timeline, composer and room permissions. The module mounts Apeiron Sidebar when Element opens a workspace and subscribes to the sorted room list through Element Module API v2. The desktop sidebar and mobile drawer expose up to twelve recent rooms. All conversations opens the native directory, including space, filter and management actions.

The sign-in screen mounts the public Apeiron LoginPage with its WebGL2 Beams effect and reduced-motion behavior, as in Limani and Apeiron Ops. Its slot displays Element's live form. The static IAM background is a fallback when WebGL2 is unavailable.

To regenerate the Chart assets, use the installed dependencies from Vasi UI with the private @apeiron/ui 0.1.105 package, React 19.2.7, Tailwind CSS 4.3.3 and Vite 8.2.2. Run from the repository root.

```sh
ln -s "$VASI_UI_DIR/node_modules" chat-ui/node_modules
node chat-ui/build.mjs
```

Commit the generated files with their source. Helm installations use the bundled assets directly.
