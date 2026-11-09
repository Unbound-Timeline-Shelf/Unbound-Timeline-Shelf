import eslint from "@eslint/js";
import eslintPluginVue from "eslint-plugin-vue";
import tseslint from "typescript-eslint";
import globals from "globals";

export default tseslint.config(
  {
    ignores: ["dist/", "node_modules/"],
  },

  eslint.configs.recommended,

  ...tseslint.configs.recommended,

  ...eslintPluginVue.configs["flat/essential"],

  {
    files: ["**/*.vue"],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser,
      },
      globals: globals.browser,
    },
  },

  {
    files: ["**/*.{js,mjs,cjs,ts}"],
    languageOptions: {
      globals: globals.browser,
    },
  },

  {
    files: ["**/*.{js,mjs,cjs,ts,tsx,vue}"],
    rules: {
      "max-lines": [
        "error",
        { max: 2000, skipBlankLines: false, skipComments: false },
      ],
    },
  },

  {
    // every date goes through utils/dates.ts so it follows the person's date
    // format (SPEC-UI-POLISH-PASS §6.6); tests/noHardcodedDateFormats.test.ts
    // is the full guard CI keeps, this flags new code in the editor
    files: ["src/**/*.{ts,vue}"],
    ignores: ["src/utils/dates.ts", "src/tests/**"],
    rules: {
      "no-restricted-properties": [
        "error",
        {
          property: "toLocaleDateString",
          message: "Use formatDay/formatInstant from utils/dates.",
        },
        {
          property: "toLocaleTimeString",
          message: "Use formatTime/formatInstant from utils/dates.",
        },
        {
          property: "toDateString",
          message: "Use dayKey or formatInstant from utils/dates.",
        },
        {
          object: "Intl",
          property: "DateTimeFormat",
          message:
            "Use utils/dates (formatInstant, deviceTimeZone) instead of Intl.DateTimeFormat.",
        },
      ],
    },
  },

  {
    // top-level route views, not reusable components — named after the
    // page they render (Login, Settings, Inbox...), so a single word is
    // the natural name here, unlike a component dropped into arbitrary
    // markup where a multi-word name avoids colliding with a native tag
    files: ["src/views/**/*.vue"],
    rules: {
      "vue/multi-word-component-names": "off",
    },
  },
);
