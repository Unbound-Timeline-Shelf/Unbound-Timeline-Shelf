# Navigation and shell acceptance checks (Playwright)

Browser checks for how the app keeps your place: a reload keeps the scroll
position, Back walks back through the sections and tabs you opened, and a
dialog with unsaved edits asks before Escape, Back or a reload discard them.
They need a running host (frontend and `/api` backend), so they are not part
of `npm run test`; the unit tests for the same behaviour are under `src/tests/`
(`scrollMemory`, `routerScroll`, `unsavedGuard`, `navigationHistory`).

`shell-and-phone.e2e.ts` covers the shell (UX-AUDIT-APP steps 4–7): the first
library card within 15 Tab stops of the skip link or of a navigation, cards as
links, one Tab stop per tab group, settings paths and the Import & sync hub,
a desktop top bar that does not repeat what the sidebar lists, the phone game
page's tab bar above the bottom navigation, the library's
wording when the server is unreachable, Home's suggested widgets and the
welcome's Later. Its unit counterparts are `keyboardFocus`, `settingsRoutes`
and `firstVisitPolish`.

```bash
cd src/frontend/e2e
npm ci
npx playwright install chromium
E2E_BASE_URL=http://127.0.0.1:5173 npx playwright test
```

| variable       | default                 | meaning                                     |
| -------------- | ----------------------- | ------------------------------------------- |
| `E2E_BASE_URL` | `http://127.0.0.1:5173` | the frontend; `/api` must reach the backend |
| `E2E_USERNAME` | `e2e-navigation`        | account used by the checks                  |
| `E2E_PASSWORD` | a synthetic value       | its password                                |

Use a throwaway instance. On a fresh host the setup step creates the first
administrator with these values; it then marks the first-visit welcome as
done for that account and adds synthetic games until the library has 120, so
the library pages and scrolls. Nothing else is changed, but the games stay.

The `desktop` project runs every check at 1440×900. The `phone` project
(390×844, touch) repeats the checks tagged `@phone`, where the back gesture is
the usual way to close a sheet.
