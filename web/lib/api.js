// Тонкая обёртка над JSON API бэкенда.

import { t } from "./i18n.js";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, body) {
  const init = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch("/api" + path, init);
  } catch (e) {
    throw new ApiError(t("Нет связи с панелью"), 0);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(data.error || t("Ошибка {status}", { status: response.status }), response.status);
  return data;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body = {}) => request("POST", path, body),
};
