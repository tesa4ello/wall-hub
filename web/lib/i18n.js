// Переводы интерфейса. Тексты в коде пишутся по-русски (исходный язык),
// t("Текст") возвращает перевод из /i18n/<язык>.json или сам текст, если
// перевода нет. {имя} в тексте заменяются на значения из params.
// Множественное число: tn(n, "{n} минута|{n} минуты|{n} минут") — формы
// перечисляются через | в порядке категорий Intl.PluralRules языка
// (для русского one|few|many, для английского one|other).
// Модули добавляют переводы в module.json, поле "i18n": {"en": {...}}.

let meta = { source: "ru", languages: [{ code: "ru", name: "Русский", locale: "ru-RU" }] };
let lang = "ru";
let catalog = {};

export async function setLanguage(code, extra = {}) {
  try {
    meta = await (await fetch("/i18n/languages.json")).json();
  } catch {
    // Без списка языков остаёмся на исходном.
  }
  lang = meta.languages.some((l) => l.code === code) ? code : meta.source;
  catalog = {};
  if (lang !== meta.source) {
    try {
      catalog = await (await fetch(`/i18n/${lang}.json`)).json();
    } catch {
      catalog = {};
    }
  }
  addTranslations(extra);
  document.documentElement.lang = lang;
}

// Переводы модулей: {"en": {...}, ...}.
export function addTranslations(byLang) {
  Object.assign(catalog, (byLang && byLang[lang]) || {});
}

export const language = () => lang;
export const languages = () => meta.languages;
export const locale = () => (meta.languages.find((l) => l.code === lang) || {}).locale || lang;

function fill(text, params) {
  return params ? text.replace(/\{(\w+)\}/g, (m, k) => (k in params ? params[k] : m)) : text;
}

export function t(text, params) {
  return fill((lang !== meta.source && catalog[text]) || text, params);
}

const PLURAL_ORDER = ["zero", "one", "two", "few", "many", "other"];

export function tn(n, text, params = {}) {
  const forms = t(text).split("|");
  const rules = new Intl.PluralRules(locale());
  const categories = PLURAL_ORDER.filter((c) => rules.resolvedOptions().pluralCategories.includes(c));
  const form = forms[categories.indexOf(rules.select(n))] ?? forms[forms.length - 1];
  return fill(form, { n, ...params });
}

// Статичные тексты в HTML: data-i18n (текст), data-i18n-placeholder.
export function translateDom(root = document) {
  root.querySelectorAll("[data-i18n]").forEach((el) => {
    el.dataset.i18n ||= el.textContent.trim();
    el.textContent = t(el.dataset.i18n);
  });
  root.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    el.dataset.i18nPlaceholder ||= el.placeholder;
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
}
