/**
 * Minimal Chrome DevTools Protocol driver for the browser suites.
 *
 * Uses Node's built-in WebSocket (Node 22+), so these tests need no npm
 * install. Assumes a Chrome with --remote-debugging-port=9222 and a
 * PromptStudio server are already running — see run.sh.
 */

const http = require('http');

const CDP_PORT = Number(process.env.CDP_PORT || 9222);
const APP_URL = process.env.APP_URL || 'http://localhost:5099/';

function httpJson(path) {
  return new Promise((resolve, reject) => {
    http
      .get({ host: '127.0.0.1', port: CDP_PORT, path }, (res) => {
        let body = '';
        res.on('data', (c) => (body += c));
        res.on('end', () => {
          try {
            resolve(JSON.parse(body));
          } catch (e) {
            reject(new Error(`bad JSON from ${path}: ${body.slice(0, 200)}`));
          }
        });
      })
      .on('error', reject);
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Session {
  constructor() {
    this.ws = null;
    this.id = 0;
    this.pending = new Map();
    this.pageErrors = [];
    this.consoleErrors = [];
  }

  async connect() {
    const targets = await httpJson('/json/list');
    const page = targets.find((t) => t.type === 'page');
    if (!page) throw new Error('no page target on the CDP endpoint');

    this.ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      this.ws.addEventListener('open', resolve, { once: true });
      this.ws.addEventListener('error', () => reject(new Error('CDP socket failed')), { once: true });
    });

    this.ws.addEventListener('message', (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
      } else if (msg.method === 'Runtime.exceptionThrown') {
        this.pageErrors.push(msg.params.exceptionDetails.exception?.description || 'unknown');
      } else if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') {
        this.consoleErrors.push(
          msg.params.args.map((a) => a.value ?? a.description ?? '?').join(' ')
        );
      }
    });

    await this.send('Runtime.enable');
    await this.send('Page.enable');
    await this.send('Network.enable');
    // Pin the viewport so layout-dependent assertions (scroll overflow) are
    // reproducible and unaffected by whatever ran before.
    await this.send('Emulation.setDeviceMetricsOverride', {
      width: 1280,
      height: 800,
      deviceScaleFactor: 1,
      mobile: false,
    });
  }

  send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++this.id;
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  /** Evaluate an async function body in the page and return its value. */
  async eval(body) {
    const r = await this.send('Runtime.evaluate', {
      expression: `(async () => { ${body} })()`,
      awaitPromise: true,
      returnByValue: true,
    });
    if (r.exceptionDetails) {
      throw new Error(
        'page eval threw: ' +
          (r.exceptionDetails.exception?.description || JSON.stringify(r.exceptionDetails))
      );
    }
    return r.result.value;
  }

  /** Load the app fresh and wait for initApp() to settle. */
  async load(url = APP_URL) {
    await this.send('Page.navigate', { url });
    await sleep(2500);
    this.pageErrors.length = 0;
    this.consoleErrors.length = 0;
  }

  async key(key) {
    const code = key === 'Escape' ? 27 : 0;
    await this.send('Input.dispatchKeyEvent', {
      type: 'keyDown',
      key,
      code: key,
      windowsVirtualKeyCode: code,
    });
    await sleep(300);
  }

  /** Record every fetch() the page makes, so "did it refetch?" is testable. */
  async startRecordingFetches() {
    await this.eval(`
      window.__calls = [];
      window.__aborts = 0;
      if (!window.__fetchPatched) {
        const original = window.fetch;
        window.__originalFetch = original;
        window.fetch = function (...args) {
          window.__calls.push(String(args[0]));
          const opts = args[1];
          if (opts && opts.signal) {
            opts.signal.addEventListener('abort', () => { window.__aborts++; });
          }
          return original.apply(this, args);
        };
        window.__fetchPatched = true;
      }
      window.__calls = [];
      return true;
    `);
  }

  async resetFetchLog() {
    await this.eval('window.__calls = []; window.__aborts = 0; return true;');
  }

  async fetchLog() {
    return this.eval('return { calls: window.__calls, aborts: window.__aborts };');
  }
}

class Report {
  constructor(title) {
    this.title = title;
    this.failures = [];
    this.passed = 0;
    console.log(`\n=== ${title} ===`);
  }

  section(name) {
    console.log(`\n[${name}]`);
  }

  check(label, condition, detail = '') {
    if (condition) this.passed += 1;
    else this.failures.push(label);
    console.log(`  ${condition ? 'PASS' : 'FAIL'}  ${label}${detail ? ` (${detail})` : ''}`);
  }

  finish(session) {
    if (session) {
      this.check('no uncaught page exceptions', session.pageErrors.length === 0,
        session.pageErrors.join('; '));
      this.check('no console errors', session.consoleErrors.length === 0,
        session.consoleErrors.join('; '));
    }
    console.log('\n' + '='.repeat(54));
    if (this.failures.length) {
      console.log(`${this.failures.length} FAILURE(S) in ${this.title}:`);
      this.failures.forEach((f) => console.log('  - ' + f));
      process.exitCode = 1;
      return false;
    }
    console.log(`ALL ${this.passed} CHECKS PASSED — ${this.title}`);
    return true;
  }
}

module.exports = { Session, Report, sleep, APP_URL };
