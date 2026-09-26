// Minimal browser stand-ins for running pillar page scripts under node:test.
// Elements are permissive stubs: any child lookup returns another stub, so a
// page script can wire its UI without a real DOM. Tests drive the page's own
// functions and observe the requests it makes through a scripted fetch.
"use strict";

const vm = require("node:vm");

const METHODS = [
  "addEventListener", "removeEventListener", "append", "appendChild", "prepend",
  "remove", "replaceChildren", "setAttribute", "removeAttribute", "focus", "blur",
  "click", "pause", "load", "play", "scrollIntoView", "insertAdjacentHTML",
  "insertBefore", "dispatchEvent",
];

function element() {
  const store = {
    value: "", textContent: "", innerHTML: "", className: "", src: "", href: "",
    title: "", download: "", checked: true, disabled: false, hidden: false,
    files: [], dataset: {}, style: {}, children: [], childNodes: [],
    classList: {
      add() {}, remove() {}, toggle() {}, contains() { return false; },
    },
    toJSON() { return null; },
    getAttribute() { return null; },
    querySelectorAll() { return []; },
    cloneNode() { return element(); },
    querySelector() { return element(); },
  };
  for (const name of METHODS) store[name] = () => undefined;
  return new Proxy(store, {
    get(target, prop) {
      if (prop in target) return target[prop];
      if (typeof prop === "symbol") return undefined;
      target[prop] = element(); // content, firstElementChild, parentNode, …
      return target[prop];
    },
    set(target, prop, value) {
      target[prop] = value;
      return true;
    },
  });
}

function response(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

// `routes` maps "METHOD path" (path without query) to a body or a function
// (request) => [status, body], which may be async to hold a reply back. Every
// request is recorded in `calls`.
function scriptedFetch(routes) {
  const calls = [];
  async function fetch(url, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    const path = String(url).split("?")[0];
    const call = { method, path, url: String(url), body: options.body };
    calls.push(call);
    const route = routes[`${method} ${path}`];
    if (route === undefined) return response(404, { detail: `no route ${method} ${path}` });
    if (typeof route === "function") return response(...(await route(call)));
    return response(200, route);
  }
  return { fetch, calls };
}

function context(fetch, extra = {}) {
  const timers = [];
  const document = {
    getElementById: () => element(),
    querySelector: () => element(),
    querySelectorAll: () => [],
    createElement: () => element(),
    createTextNode: () => element(),
    addEventListener() {},
    body: element(),
  };
  const ctx = {
    document,
    window: {},
    fetch,
    console,
    FormData: class { append() {} },
    URL: { createObjectURL: () => "blob:x", revokeObjectURL() {} },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    confirm: () => { throw new Error("confirm() must not be called here"); },
    setInterval: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearInterval() {},
    setTimeout: (fn) => { fn(); return 0; },
    clearTimeout() {},
    Date,
    ...extra,
  };
  ctx.window = ctx;
  return { ctx: vm.createContext(ctx), timers };
}

function run(source, ctx) {
  vm.runInContext(source, ctx);
  return (expr) => vm.runInContext(expr, ctx);
}

// Wait until every queued promise callback has run.
async function settle() {
  for (let i = 0; i < 20; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

module.exports = { element, response, scriptedFetch, context, run, settle };
