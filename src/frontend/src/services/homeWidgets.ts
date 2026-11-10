import { validateField, type UiField, type UiValues } from "./pluginUi";

export interface HomeWidgetChoice {
  id: string;
  title: string;
  description: string;
  available?: boolean;
}

export function widgetConfiguration(
  fields: UiField[],
  saved: UiValues = {},
): UiValues {
  const values: UiValues = {};
  for (const field of fields) {
    if (field.secret || field.type === "password") continue;
    const candidate = saved[field.id];
    if (candidate !== undefined && validateField(field, candidate) === null)
      values[field.id] = candidate;
    else if (field.default != null) values[field.id] = field.default;
  }
  return values;
}

export const CORE_HOME_WIDGETS: HomeWidgetChoice[] = [
  {
    id: "continue-playing",
    title: "Continue playing",
    description: "Your games in progress, most recently played first.",
  },
  {
    id: "recently-added",
    title: "Recently added",
    description: "The newest games in your library.",
  },
  {
    id: "library-summary",
    title: "Library summary",
    description: "Game, favorite and collection counts.",
  },
  {
    id: "random-picker",
    title: "Random picker",
    description: "Choose a game with your own filters.",
  },
  {
    id: "weekly-digest",
    title: "This week",
    description: "Games played, achievements and metadata activity.",
  },
  {
    id: "backlog",
    title: "Revisit your backlog",
    description: "Games added over 90 days ago that you have not played.",
  },
  {
    id: "on-this-day",
    title: "On this day",
    description: "Games added on this date in previous years.",
  },
  {
    id: "getting-started",
    title: "Getting started",
    description: "Connect a library and favorite a game.",
  },
];

// What Home shows before the person has chosen (UX-AUDIT-APP A22): their
// games in progress and the newest additions when the library has games, a
// getting-started card when it is empty. Choosing in Customize Home, even
// choosing nothing, replaces the suggestion for good.
export const SUGGESTED_HOME_WIDGETS = [
  "continue-playing",
  "recently-added",
  "library-summary",
];
export const EMPTY_LIBRARY_HOME_WIDGETS = ["getting-started"];

/** The widget ids Home shows, or null while the library is still loading. */
export function homeWidgetIds(
  preferences: { home_widgets: string[]; home_widgets_customized?: boolean },
  library: { loaded: boolean; size: number },
): string[] | null {
  if (preferences.home_widgets.length || preferences.home_widgets_customized)
    return preferences.home_widgets;
  if (!library.loaded) return null;
  return library.size ? SUGGESTED_HOME_WIDGETS : EMPTY_LIBRARY_HOME_WIDGETS;
}

/** Home is still showing the suggestion rather than the person's choice. */
export function showsSuggestedHome(preferences: {
  home_widgets: string[];
  home_widgets_customized?: boolean;
}): boolean {
  return (
    !preferences.home_widgets.length && !preferences.home_widgets_customized
  );
}

export function selectedHomeWidgets(
  ids: string[],
  choices: HomeWidgetChoice[],
): HomeWidgetChoice[] {
  const available = new Map(choices.map((choice) => [choice.id, choice]));
  return ids.map(
    (id) =>
      available.get(id) ??
      (id === "goals"
        ? available.get("plugin:official.collectors-archive:goals")
        : undefined) ?? {
        id,
        title: id.startsWith("collection:")
          ? id.slice(11)
          : "Unavailable widget",
        description:
          "This widget is unavailable. Your selection is retained until it returns or you remove it.",
        available: false,
      },
  );
}
