const PALETTE = ["#1f6b4a", "#8c4a2f", "#245f8a", "#6e5a28", "#3e6b62", "#7a3e55"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const STORAGE_KEY = "laterledger.tools.v1";
const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

const CADENCE = {
  once: "One time",
  weekly: "Each week",
  biweekly: "Every two weeks",
  monthly: "Each month",
  quarterly: "Each quarter",
  yearly: "Each year",
};

const MIXES = [
  { id: "stocks", title: "Mostly stocks", detail: "A long-lived US stock fund" },
  { id: "balanced", title: "A balanced mix", detail: "60% stocks, 40% bonds" },
  { id: "bonds", title: "Mostly bonds", detail: "20% stocks, 80% bonds" },
];

const TITLES = {
  back: "What if I had",
  ahead: "If I keep going",
  last: "How long it lasts",
};

const form = document.getElementById("controls");
const resultsEl = document.getElementById("results");
const quotes = {};
const quoteJobs = {};
const results = { back: null, ahead: null, last: null };
const problems = { back: "", ahead: "", last: "" };
let tab = "back";
let state = loadState();

form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (tab === "back") runBack(false);
  if (tab === "ahead") runAhead();
  if (tab === "last") runLast();
});

form.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-action]");
  if (!trigger) return;
  event.preventDefault();
  const action = trigger.dataset.action;
  captureActive();
  if (action === "add-sleeve") addSleeve();
  if (action === "remove-sleeve") removeSleeve(Number(trigger.dataset.index));
  if (action === "add-period") addPeriod();
  if (action === "add-lump") addLump();
  if (action === "remove-period") state.back.periods.splice(Number(trigger.dataset.index), 1);
  if (action === "set-mode") {
    const period = state.back.periods[Number(trigger.dataset.index)];
    const mode = trigger.dataset.mode;
    if (period && period.mode !== mode) convertPeriod(period, mode);
  }
  if (action === "mix") {
    const bucket = tab === "last" ? state.last : state.ahead;
    bucket.mix = trigger.dataset.mix;
  }
  if (action === "preview") {
    render();
    runBack(true);
    return;
  }
  render();
  if (action === "add-sleeve") {
    const inputs = form.querySelectorAll(".symbol");
    inputs[inputs.length - 1].focus();
  }
});

form.addEventListener("input", (event) => {
  problems[tab] = "";
  if (!results[tab]) {
    if (document.getElementById("error")) renderOutput();
  } else {
    const err = document.getElementById("error");
    if (err) err.remove();
  }
  if (event.target.matches(".weight, .dollars, .period-amount")) refreshPeriodNotes();
  if (event.target.id === "last-starting") state.last.carried = false;
});

form.addEventListener("change", (event) => {
  if (event.target.id === "rebalance") {
    document.getElementById("threshold-wrap").hidden = event.target.value !== "threshold";
  }
  if (event.target.id === "compare" || event.target.classList.contains("symbol")) {
    captureActive();
    render();
    return;
  }
  if (event.target.id === "start") {
    const linked = form.querySelector(".period-start[data-linked='start']");
    if (linked) linked.value = event.target.value;
  }
  if (event.target.id === "end") {
    const linked = form.querySelector(".period-end[data-linked='end']");
    if (linked) linked.value = event.target.value;
  }
  if (event.target.classList.contains("period-start") && event.target.value !== form.querySelector("#start").value) {
    delete event.target.dataset.linked;
  }
  if (event.target.classList.contains("period-end") && event.target.value !== form.querySelector("#end").value) {
    delete event.target.dataset.linked;
  }
  if (event.target.id === "start" || event.target.id === "end" || event.target.classList.contains("period-start") || event.target.classList.contains("period-end")) {
    captureActive();
    refreshPeriodNotes();
    paintQuotes();
  }
  if (event.target.classList.contains("period-frequency")) {
    if (event.target.value === "once") {
      captureActive();
      render();
      return;
    }
    const period = event.target.closest(".period");
    const label = cadenceLabel(event.target.value);
    period.querySelectorAll(".cadence").forEach((node) => {
      node.textContent = label;
    });
    refreshPeriodNotes();
  }
});

document.querySelector(".tabs").addEventListener("click", (event) => {
  const next = event.target.closest("[data-tab]")?.dataset.tab;
  if (!next || next === tab) return;
  captureActive();
  save();
  tab = next;
  history.replaceState(null, "", `#${next}`);
  render();
});

resultsEl.addEventListener("change", (event) => {
  if (event.target.id !== "today-dollars") return;
  state.back.todayDollars = event.target.checked;
  save();
  renderOutput();
});

resultsEl.addEventListener("click", (event) => {
  if (event.target.dataset.action !== "use-ending") return;
  const ending = results.back?.stats?.ending_value;
  if (ending == null) return;
  state.last.starting = Math.round(ending);
  state.last.carried = true;
  tab = "last";
  history.replaceState(null, "", "#last");
  save();
  render();
});

const hashed = location.hash.replace("#", "");
if (TITLES[hashed]) tab = hashed;
render();

function loadState() {
  const fresh = blankState();
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    const back = cleanBack(saved?.back);
    if (back) fresh.back = back;
    if (saved?.ahead) fresh.ahead = { ...fresh.ahead, ...saved.ahead };
    if (saved?.last) fresh.last = { ...fresh.last, ...saved.last, carried: false };
  } catch (_error) {
    return fresh;
  }
  return fresh;
}

function blankState() {
  const end = new Date();
  const start = new Date();
  start.setFullYear(end.getFullYear() - 30);
  const startIso = isoDate(start);
  const endIso = isoDate(end);
  return {
    back: {
      start: startIso,
      end: endIso,
      rebalance: "yearly",
      threshold: 5,
      compare: false,
      benchmark: "VFINX",
      todayDollars: false,
      sleeves: [
        { symbol: "VFINX", drip: true, expense: 0 },
        { symbol: "VBMFX", drip: true, expense: 0 },
      ],
      periods: twoStretches(startIso, endIso),
    },
    ahead: {
      mix: "balanced",
      years: 30,
      starting: 10000,
      annualContribution: 6000,
      annualWithdrawal: 0,
      glide: false,
    },
    last: {
      mix: "balanced",
      years: 30,
      starting: 500000,
      annualSpend: 30000,
      inflation: true,
      carried: false,
    },
  };
}

function cleanBack(back) {
  if (!back?.start || !back?.end || !Array.isArray(back.sleeves) || !back.sleeves.length) return null;
  if (!Array.isArray(back.periods) || !back.periods.length) return null;
  const sleeves = back.sleeves.map((sleeve) => ({
    symbol: String(sleeve.symbol || "").toUpperCase(),
    drip: sleeve.drip !== false,
    expense: Number(sleeve.expense) || 0,
  }));
  const count = sleeves.length;
  const periods = [];
  for (const period of back.periods) {
    const percents = Array.isArray(period.percents) ? period.percents.map(Number) : [];
    const dollars = Array.isArray(period.dollars) ? period.dollars.map(Number) : [];
    if (!period.start || percents.length !== count || dollars.length !== count) return null;
    periods.push({
      start: period.start,
      end: period.end || period.start,
      frequency: period.frequency || "monthly",
      mode: period.mode === "dollars" ? "dollars" : "percent",
      amount: Number(period.amount) || 0,
      percents,
      dollars,
    });
  }
  return {
    start: back.start,
    end: back.end,
    rebalance: back.rebalance || "yearly",
    threshold: Number(back.threshold) || 5,
    compare: Boolean(back.compare),
    benchmark: back.benchmark || "VFINX",
    todayDollars: Boolean(back.todayDollars),
    sleeves,
    periods,
  };
}

function save() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ back: state.back, ahead: state.ahead, last: state.last }));
  } catch (_error) {
    /* a full browser store should not block the tool */
  }
}

function render() {
  document.querySelectorAll(".tabs button").forEach((button) => {
    button.setAttribute("aria-selected", button.dataset.tab === tab ? "true" : "false");
  });
  document.title = `Later Ledger — ${TITLES[tab]}`;
  if (tab === "back") renderBack();
  if (tab === "ahead") renderAhead();
  if (tab === "last") renderLast();
  renderOutput();
  if (tab === "back") refreshQuotes();
}

function renderBack() {
  const back = state.back;
  const thresholdHidden = back.rebalance !== "threshold" ? "hidden" : "";
  const compareHidden = back.compare ? "" : "hidden";
  form.innerHTML = `
    <section class="block">
      <h2>What if I had</h2>
      <p class="help">Say what you would have put in, and when. The chart is what that would have been worth at past prices.</p>
      <div class="row">
        <label>From<input id="start" type="date" value="${esc(back.start)}" required></label>
        <label>Through<input id="end" type="date" value="${esc(back.end)}" required></label>
      </div>
    </section>
    <section class="block">
      <h2>Holdings</h2>
      <p class="help">Type a symbol, then choose whether its dividends buy more shares. Yearly cost is the fund's fee. 0.03 means 3 cents a year for every $100. Leave 0 to ignore fees.</p>
      ${back.sleeves.map((sleeve, index) => `
        <div class="sleeve">
          <div class="row">
            <label class="grow">Symbol<input class="symbol" value="${esc(sleeve.symbol)}" autocomplete="off" spellcheck="false"></label>
            <label>Yearly cost %<input class="expense" type="number" min="0" max="5" step="0.01" value="${esc(sleeve.expense)}"></label>
            <button type="button" class="ghost" data-action="remove-sleeve" data-index="${index}" ${back.sleeves.length === 1 ? "disabled" : ""}>Remove</button>
          </div>
          <p class="quote" data-symbol="${esc(sleeve.symbol)}"></p>
          <label class="check"><input class="drip" type="checkbox" ${sleeve.drip ? "checked" : ""}> Reinvest dividends</label>
        </div>
      `).join("")}
      <button type="button" data-action="add-sleeve">Add a holding</button>
    </section>
    <section class="block">
      <h2>What you added</h2>
      <p class="help">The account starts empty. A stretch adds money on a repeating schedule. A lump sum is invested once, on the date you choose, in addition to any stretch that covers that day.</p>
      ${back.periods.map((period, index) => periodCard(period, index)).join("")}
      <div class="buttons">
        <button type="button" data-action="add-period">Add a stretch</button>
        <button type="button" data-action="add-lump">Add a lump sum</button>
      </div>
    </section>
    <section class="block">
      <h2>Keeping the mix</h2>
      <p class="help">Rebalancing uses the split of money already invested. A percent you leave blank is not cash you had saved. If a fund did not exist yet, that income is not invested and is not counted as cash.</p>
      <label>Rebalance
        <select id="rebalance">
          ${options(["never", "monthly", "quarterly", "yearly", "threshold"], back.rebalance, {
            never: "Don't rebalance",
            monthly: "Every month",
            quarterly: "Every quarter",
            yearly: "Every year",
            threshold: "When a holding drifts",
          })}
        </select>
      </label>
      <label id="threshold-wrap" ${thresholdHidden}>Drift (percentage points)
        <input id="threshold" type="number" min="1" max="50" step="1" value="${esc(back.threshold)}">
      </label>
    </section>
    <section class="block">
      <h2>A comparison</h2>
      <label class="check"><input id="compare" type="checkbox" ${back.compare ? "checked" : ""}> Show the same deposits in one fund</label>
      <div ${compareHidden}>
        <label>Fund<input id="benchmark" value="${esc(back.benchmark)}" autocomplete="off" spellcheck="false"></label>
        <p class="quote" data-symbol="${esc(back.compare ? back.benchmark : "")}"></p>
        <p class="help">Every amount you add or take out is placed in this one fund, so you can see that path beside your mix.</p>
      </div>
    </section>
    <div class="buttons">
      <button class="primary" type="submit" id="run-back">See the past</button>
      <button type="button" id="run-sample" data-action="preview">Try sample prices</button>
    </div>
  `;
  refreshPeriodNotes();
  paintQuotes();
}

function renderAhead() {
  const ahead = state.ahead;
  const glideHelp = ahead.mix === "bonds"
    ? "This mix is already mostly bonds, so there is nowhere further to shift."
    : "Mostly stocks eases toward about 60% stocks. A balanced mix eases toward about 40% stocks.";
  form.innerHTML = `
    <section class="block">
      <h2>If I keep going</h2>
      <p class="help">Pick a mix of ordinary funds. The chart reshuffles how that mix behaved in past years. It is a range, not a forecast.</p>
      <div class="mixes">
        ${MIXES.map((mix) => `
          <button type="button" data-action="mix" data-mix="${mix.id}" class="${ahead.mix === mix.id ? "on" : ""}" aria-pressed="${ahead.mix === mix.id ? "true" : "false"}">
            ${esc(mix.title)}
            <small>${esc(mix.detail)}</small>
          </button>
        `).join("")}
      </div>
    </section>
    <section class="block">
      <h2>The amounts</h2>
      <label>I have this now<input id="ahead-starting" type="number" min="0" step="1000" value="${esc(ahead.starting)}"></label>
      <div class="row">
        <label>I'll add each year<input id="ahead-add" type="number" min="0" step="1000" value="${esc(ahead.annualContribution)}"></label>
        <label>I'll spend each year<input id="ahead-spend" type="number" min="0" step="1000" value="${esc(ahead.annualWithdrawal)}"></label>
      </div>
      <label>For how many years<input id="ahead-years" type="number" min="1" max="60" value="${esc(ahead.years)}"></label>
      <label class="check"><input id="ahead-glide" type="checkbox" ${ahead.glide ? "checked" : ""} ${ahead.mix === "bonds" ? "disabled" : ""}> Shift toward bonds over these years</label>
      <p class="help">${esc(glideHelp)}</p>
    </section>
    <div class="buttons">
      <button class="primary" type="submit" id="run-ahead">Show a range</button>
    </div>
  `;
}

function renderLast() {
  const last = state.last;
  form.innerHTML = `
    <section class="block">
      <h2>How long it lasts</h2>
      <p class="help">Start with a balance and a yearly amount to spend. The tool walks through each past stretch of that length and counts how often the money would have lasted.</p>
      ${last.carried ? `<p class="banner">The starting amount is the ending value from What if I had.</p>` : ""}
      <div class="mixes">
        ${MIXES.map((mix) => `
          <button type="button" data-action="mix" data-mix="${mix.id}" class="${last.mix === mix.id ? "on" : ""}" aria-pressed="${last.mix === mix.id ? "true" : "false"}">
            ${esc(mix.title)}
            <small>${esc(mix.detail)}</small>
          </button>
        `).join("")}
      </div>
    </section>
    <section class="block">
      <h2>The amounts</h2>
      <label>I have<input id="last-starting" type="number" min="0" step="1000" value="${esc(last.starting)}"></label>
      <label>I'll spend each year<input id="last-spend" type="number" min="0" step="1000" value="${esc(last.annualSpend)}"></label>
      <label>Check stretches of this many years<input id="last-years" type="number" min="1" max="50" value="${esc(last.years)}"></label>
      <label class="check"><input id="last-inflation" type="checkbox" ${last.inflation ? "checked" : ""}> Spending rises with the cost of living</label>
    </section>
    <div class="buttons">
      <button class="primary" type="submit" id="run-last">Check the past</button>
    </div>
  `;
}

function periodCard(period, index) {
  const back = state.back;
  const lump = period.frequency === "once";
  const cadence = lump ? "Amount" : cadenceLabel(period.frequency);
  const stretchNumber = back.periods.slice(0, index + 1).filter((item) => item.frequency !== "once").length;
  const linkedStart = !lump && period.start === back.start && index === back.periods.findIndex((item) => item.frequency !== "once") ? "data-linked=\"start\"" : "";
  const lastRecurring = back.periods.reduce((found, item, itemIndex) => (item.frequency === "once" ? found : itemIndex), -1);
  const linkedEnd = !lump && index === lastRecurring && period.end === back.end ? "data-linked=\"end\"" : "";
  return `
    <article class="period${lump ? " lump" : ""}" data-mode="${period.mode}">
      <div class="period-head">
        <p class="period-title">${lump ? "Lump sum" : `Stretch ${stretchNumber}`}</p>
        <button type="button" class="ghost" data-action="remove-period" data-index="${index}" ${back.periods.length === 1 ? "disabled" : ""}>Remove</button>
      </div>
      <div class="row">
        <label>${lump ? "On" : "From"}<input class="period-start" type="date" value="${esc(period.start)}" ${linkedStart}></label>
        ${lump ? `<input class="period-end" type="hidden" value="${esc(period.start)}">` : `<label class="through-field">Through<input class="period-end" type="date" value="${esc(period.end || "")}" ${linkedEnd}></label>`}
      </div>
      ${lump ? `<input class="period-frequency" type="hidden" value="once">` : `<label>How often
        <select class="period-frequency">
          ${options(["monthly", "weekly", "biweekly", "quarterly", "yearly", "once"], period.frequency, CADENCE)}
        </select>
      </label>`}
      <div class="toggle" role="group" aria-label="How to enter ${lump ? "lump sum" : "stretch"} ${index + 1}">
        <button type="button" data-action="set-mode" data-mode="percent" data-index="${index}" class="${period.mode === "percent" ? "on" : ""}">Percent</button>
        <button type="button" data-action="set-mode" data-mode="dollars" data-index="${index}" class="${period.mode === "dollars" ? "on" : ""}">Dollars</button>
      </div>
      <div class="percent-fields" ${period.mode === "percent" ? "" : "hidden"}>
        <label><span class="cadence">${esc(cadence)}</span>
          <input class="period-amount" type="number" step="1" value="${esc(period.amount)}">
        </label>
        ${splitRows(period.percents, "weight", "%", lump ? "" : cadence)}
        <p class="sum"></p>
      </div>
      <div class="dollar-fields" ${period.mode === "dollars" ? "" : "hidden"}>
        ${splitRows(period.dollars, "dollars", "$", lump ? "" : cadence)}
        <p class="sum"></p>
      </div>
      <p class="overlap"></p>
    </article>
  `;
}

function splitRows(values, klass, unit, cadence) {
  return state.back.sleeves.map((sleeve, index) => `
    <div class="split-row">
      <span class="split-name">${esc(sleeve.symbol || "Holding " + (index + 1))}</span>
      <input class="${klass}" type="number" step="1" value="${esc(values[index] ?? 0)}" aria-label="${esc(sleeve.symbol || "Holding")} ${unit === "%" ? "percent" : "dollars"}">
      <span class="split-unit">${unit === "$" ? esc(cadence) : "%"}</span>
    </div>
  `).join("");
}

function options(values, selected, labels) {
  return values.map((value) => `<option value="${value}" ${value === selected ? "selected" : ""}>${esc(labels[value])}</option>`).join("");
}

function captureActive() {
  if (tab === "back" && form.querySelector("#start")) captureBack();
  if (tab === "ahead" && form.querySelector("#ahead-starting")) captureAhead();
  if (tab === "last" && form.querySelector("#last-starting")) captureLast();
  save();
}

function captureBack() {
  const back = state.back;
  back.start = form.querySelector("#start").value;
  back.end = form.querySelector("#end").value;
  back.rebalance = form.querySelector("#rebalance").value;
  back.threshold = Number(form.querySelector("#threshold").value);
  back.compare = form.querySelector("#compare").checked;
  const benchmark = form.querySelector("#benchmark");
  if (benchmark) back.benchmark = benchmark.value.trim().toUpperCase();
  back.sleeves = [...form.querySelectorAll(".sleeve")].map((row) => ({
    symbol: row.querySelector(".symbol").value.trim().toUpperCase(),
    drip: row.querySelector(".drip").checked,
    expense: Number(row.querySelector(".expense").value) || 0,
  }));
  back.periods = [...form.querySelectorAll(".period")].map((row) => ({
    start: row.querySelector(".period-start").value,
    end: row.querySelector(".period-end").value,
    frequency: row.querySelector(".period-frequency").value,
    mode: row.dataset.mode,
    amount: Number(row.querySelector(".period-amount").value),
    percents: [...row.querySelectorAll(".weight")].map((input) => Number(input.value)),
    dollars: [...row.querySelectorAll(".dollars")].map((input) => Number(input.value)),
  }));
}

function captureAhead() {
  state.ahead.starting = Number(form.querySelector("#ahead-starting").value) || 0;
  state.ahead.annualContribution = Number(form.querySelector("#ahead-add").value) || 0;
  state.ahead.annualWithdrawal = Number(form.querySelector("#ahead-spend").value) || 0;
  state.ahead.years = Number(form.querySelector("#ahead-years").value) || 0;
  state.ahead.glide = form.querySelector("#ahead-glide").checked;
}

function captureLast() {
  state.last.starting = Number(form.querySelector("#last-starting").value) || 0;
  state.last.annualSpend = Number(form.querySelector("#last-spend").value) || 0;
  state.last.years = Number(form.querySelector("#last-years").value) || 0;
  state.last.inflation = form.querySelector("#last-inflation").checked;
}

function addSleeve() {
  state.back.sleeves.push({ symbol: "", drip: true, expense: 0 });
  for (const period of state.back.periods) {
    period.percents.push(0);
    period.dollars.push(0);
  }
}

function removeSleeve(index) {
  state.back.sleeves.splice(index, 1);
  for (const period of state.back.periods) {
    period.percents.splice(index, 1);
    period.dollars.splice(index, 1);
  }
}

function addPeriod() {
  const back = state.back;
  const recurring = back.periods.filter((period) => period.frequency !== "once");
  const last = recurring[recurring.length - 1] || back.periods[back.periods.length - 1];
  let start = addYears(last.start, 10);
  if (start >= back.end) start = addDays(last.start, 1);
  if (start >= back.end) start = back.end;
  const previousEnd = addDays(start, -1);
  if (last.frequency !== "once" && last.end >= start && previousEnd >= last.start) last.end = previousEnd;
  const scale = 2;
  back.periods.push({
    start,
    end: back.end,
    frequency: "monthly",
    mode: last.mode,
    amount: Math.round((Number(last.amount) || 0) * scale),
    percents: last.percents.slice(),
    dollars: last.dollars.map((value) => Math.round((Number(value) || 0) * scale)),
  });
  sortPeriods();
}

function addLump() {
  const back = state.back;
  const template = back.periods.find((period) => period.frequency !== "once") || back.periods[0];
  let start = addYears(back.start, 5);
  if (!start || start < back.start) start = back.start;
  if (start > back.end) start = back.end;
  const amount = 10000;
  const fractions = mixFractions(template);
  back.periods.push({
    start,
    end: start,
    frequency: "once",
    mode: "percent",
    amount,
    percents: fractions.map((value) => Math.round(value * 1000) / 10),
    dollars: fractions.map((value) => Math.round(amount * value)),
  });
  sortPeriods();
}

function sortPeriods() {
  state.back.periods.sort((a, b) => a.start.localeCompare(b.start) || (a.frequency === "once") - (b.frequency === "once"));
}

function mixFractions(period) {
  const source = period.mode === "dollars" ? period.dollars : period.percents;
  const values = source.map((value) => Math.max(Number(value) || 0, 0));
  const total = values.reduce((sum, value) => sum + value, 0);
  if (!total) return values.map(() => 0);
  return values.map((value) => value / total);
}

function convertPeriod(period, mode) {
  if (mode === "dollars") {
    const total = Number(period.amount) || 0;
    period.dollars = period.percents.map((value) => Math.round(total * (Number(value) || 0) / 100));
  } else {
    const total = period.dollars.reduce((sum, value) => sum + (Number(value) || 0), 0);
    period.amount = Math.round(total);
    if (total) {
      const raw = period.dollars.map((value) => (Number(value) || 0) / total * 100);
      const rounded = raw.map((value) => Math.round(value * 10) / 10);
      const drift = Math.round((100 - rounded.reduce((sum, value) => sum + value, 0)) * 10) / 10;
      const largest = rounded.reduce((best, value, index) => (Math.abs(value) > Math.abs(rounded[best]) ? index : best), 0);
      rounded[largest] = Math.round((rounded[largest] + drift) * 10) / 10;
      period.percents = rounded;
    }
  }
  period.mode = mode;
}

function refreshPeriodNotes() {
  const cards = [...document.querySelectorAll(".period")];
  if (!cards.length) return;
  const ranges = cards.map((card) => ({
    start: card.querySelector(".period-start").value,
    end: card.querySelector(".period-frequency").value === "once"
      ? card.querySelector(".period-start").value
      : card.querySelector(".period-end").value,
    recurring: card.querySelector(".period-frequency").value !== "once",
  }));
  cards.forEach((card, index) => {
    const percentBox = card.querySelector(".percent-fields");
    const dollarBox = card.querySelector(".dollar-fields");
    const frequency = card.querySelector(".period-frequency").value;
    const cadence = frequency === "once" ? "on this date" : cadenceLabel(frequency).toLowerCase();
    const percentSum = [...percentBox.querySelectorAll(".weight")].reduce((sum, input) => sum + (Number(input.value) || 0), 0);
    const percentLabel = percentBox.querySelector(".sum");
    const amount = Number(card.querySelector(".period-amount").value) || 0;
    if (percentSum > 100.05) {
      percentLabel.textContent = `Those add up to ${percentSum.toFixed(1)}%. Use 100% or less.`;
      percentLabel.className = "sum bad";
    } else if (percentSum < 99.95) {
      const investedNow = amount * percentSum / 100;
      percentLabel.textContent = frequency === "once"
        ? `${money.format(investedNow)} is invested on this date. The other ${(100 - percentSum).toFixed(1)}% is not added.`
        : `${money.format(investedNow)} ${cadence} is invested as it arrives. The other ${(100 - percentSum).toFixed(1)}% is not added.`;
      percentLabel.className = "sum";
    } else {
      percentLabel.textContent = frequency === "once"
        ? `${money.format(amount)} is invested on this date.`
        : `${money.format(amount)} ${cadence}, fully invested.`;
      percentLabel.className = "sum";
    }
    const dollarSum = [...dollarBox.querySelectorAll(".dollars")].reduce((sum, input) => sum + (Number(input.value) || 0), 0);
    dollarBox.querySelector(".sum").textContent = frequency === "once"
      ? `${money.format(dollarSum)} invested on this date.`
      : `${money.format(dollarSum)} ${cadence} across these holdings.`;
    const note = card.querySelector(".overlap");
    const mine = ranges[index];
    const clash = ranges.some((other, otherIndex) => (
      otherIndex !== index && mine.recurring && other.recurring && mine.start && mine.end && other.start && other.end
      && mine.start <= other.end && other.start <= mine.end
    ));
    note.textContent = clash ? "This overlaps another stretch, so both amounts are invested then." : "";
  });
}

function payload() {
  const back = state.back;
  const symbols = back.sleeves.map((sleeve) => sleeve.symbol);
  const regimes = [];
  const flows = [];
  for (const period of back.periods) {
    const end = period.frequency === "once" ? null : period.end || null;
    const { weights, amount, withdrawals } = splitPeriod(period, symbols);
    const invested = Object.values(weights).reduce((sum, value) => sum + value, 0);
    if (period.frequency !== "once" && period.start && invested > 0) {
      regimes.push({ date: period.start, weights });
    }
    if (amount) {
      flows.push({ amount, frequency: period.frequency, start: period.start, end, allocation: weights });
    }
    for (const withdrawal of withdrawals) {
      flows.push({
        amount: withdrawal.amount,
        frequency: period.frequency,
        start: period.start,
        end,
        allocation: withdrawal.allocation,
      });
    }
  }
  return {
    start: back.start,
    end: back.end,
    listing_policy: "skip",
    rebalance: { mode: back.rebalance, threshold: Number(back.threshold) / 100 },
    sleeves: back.sleeves.map((sleeve) => ({
      symbol: sleeve.symbol,
      drip: sleeve.drip,
      expense_ratio: (Number(sleeve.expense) || 0) / 100,
    })),
    regimes,
    flows,
    benchmark: back.compare ? back.benchmark : null,
  };
}

function splitPeriod(period, symbols) {
  if (period.mode === "percent") {
    const weights = Object.fromEntries(symbols.map((symbol, index) => [symbol, (Number(period.percents[index]) || 0) / 100]));
    return { weights, amount: Number(period.amount) || 0, withdrawals: [] };
  }
  const dollars = period.dollars.map((value) => Number(value) || 0);
  const positive = dollars.map((value) => Math.max(value, 0));
  const positiveTotal = positive.reduce((sum, value) => sum + value, 0);
  const weights = Object.fromEntries(symbols.map((symbol, index) => [
    symbol,
    positiveTotal ? positive[index] / positiveTotal : 0,
  ]));
  const withdrawals = [];
  dollars.forEach((value, index) => {
    if (value < 0) {
      withdrawals.push({
        amount: value,
        allocation: Object.fromEntries(symbols.map((symbol, symbolIndex) => [symbol, symbolIndex === index ? 1 : 0])),
      });
    }
  });
  return { weights, amount: positiveTotal, withdrawals };
}

function validateBack(body) {
  const back = state.back;
  if (!body.start || !body.end) return "Choose a start and an end date.";
  if (body.end < body.start) return "The end date is before the start date.";
  if (!body.sleeves.length) return "Add at least one holding.";
  if (body.sleeves.some((sleeve) => !sleeve.symbol)) return "Enter a symbol for each holding.";
  if (body.sleeves.some((sleeve) => sleeve.expense_ratio < 0 || sleeve.expense_ratio > 0.05)) {
    return "Yearly cost is a percent from 0 to 5. A cheap stock fund is about 0.03.";
  }
  const unique = new Set(body.sleeves.map((sleeve) => sleeve.symbol));
  if (unique.size !== body.sleeves.length) return "Each holding can appear once.";
  for (const sleeve of back.sleeves) {
    if (quotes[sleeve.symbol]?.error) return quotes[sleeve.symbol].error;
  }
  if (back.compare) {
    if (!back.benchmark) return "Enter the comparison fund.";
    if (quotes[back.benchmark]?.error) return quotes[back.benchmark].error;
  }
  const allLate = back.sleeves.length > 0 && back.sleeves.every((sleeve) => {
    const listed = quotes[sleeve.symbol]?.listed_on;
    return listed && back.end < listed;
  });
  if (allLate) return "Every holding starts after this chart ends. Move the end date, or pick a fund that existed then.";
  if (!back.periods.length) return "Add at least one stretch or lump sum.";
  const starts = new Set();
  for (const period of back.periods) {
    if (!period.start) return "Each entry needs a date.";
    if (period.frequency === "once") {
      if (period.start < body.start || period.start > body.end) {
        return "A lump sum is outside the chart window. Move its date inside the window.";
      }
    } else {
      if (period.end && period.end < period.start) return "A stretch ends before it starts.";
      if (period.start > body.end) return "A stretch starts after the chart window ends.";
      if (starts.has(period.start)) return "Two stretches start on the same date.";
      starts.add(period.start);
    }
    if (period.mode === "percent") {
      if (period.percents.some((value) => Number(value) < 0)) {
        return "Use a minus on the amount to withdraw. Percentages stay at zero or above.";
      }
      const sum = period.percents.reduce((total, value) => total + (Number(value) || 0), 0);
      if (sum > 100.05) return `A split adds up to ${sum.toFixed(1)}%. Use 100% or less.`;
    }
    if (Math.abs(Number(period.amount) || 0) > 1e12) return "Use an amount below one trillion.";
  }
  if (back.rebalance === "threshold" && !(back.threshold > 0 && back.threshold < 100)) {
    return "Drift must be between 1 and 99 percentage points.";
  }
  if (!body.flows.length) return "Enter an amount for at least one stretch or lump sum.";
  return "";
}

async function runBack(sample) {
  captureBack();
  save();
  const body = payload();
  const problem = validateBack(body);
  if (problem) {
    showError(problem);
    return;
  }
  await runRequest("back", "run-back", () => post(sample ? "/api/preview" : "/api/backtest", body));
}

async function runAhead() {
  captureAhead();
  save();
  const ahead = state.ahead;
  if (ahead.starting < 0 || ahead.annualContribution < 0 || ahead.annualWithdrawal < 0) {
    showError("Amounts are entered as zero or more.");
    return;
  }
  if (!(ahead.years >= 1 && ahead.years <= 60)) {
    showError("Looking ahead must be between 1 and 60 years.");
    return;
  }
  await runRequest("ahead", "run-ahead", () => post("/api/outlook", {
    mix: ahead.mix,
    years: ahead.years,
    starting: ahead.starting,
    annual_contribution: ahead.annualContribution,
    annual_withdrawal: ahead.annualWithdrawal,
    glide: ahead.mix === "bonds" ? false : ahead.glide,
    trials: 500,
    source: "market",
  }));
}

async function runLast() {
  captureLast();
  save();
  const last = state.last;
  if (last.starting < 0 || last.annualSpend < 0) {
    showError("The starting amount and the yearly spending are zero or more.");
    return;
  }
  if (!(last.years >= 1 && last.years <= 50)) {
    showError("The stretch must be between 1 and 50 years.");
    return;
  }
  await runRequest("last", "run-last", () => post("/api/lasting", {
    mix: last.mix,
    years: last.years,
    starting: last.starting,
    annual_spend: last.annualSpend,
    inflation: last.inflation,
    source: "market",
  }));
}

async function runRequest(which, buttonId, job) {
  setBusy(buttonId, true);
  problems[which] = "";
  try {
    results[which] = await job();
    renderOutput();
  } catch (error) {
    const message = error?.message && !/fetch|network/i.test(error.message) ? error.message : "The server did not respond.";
    showError(message);
  } finally {
    setBusy(buttonId, false);
  }
}

async function post(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(errorText(data));
  return data;
}

function setBusy(buttonId, busy) {
  const button = document.getElementById(buttonId);
  if (!button) return;
  button.disabled = busy;
  if (buttonId === "run-back") button.textContent = busy ? "Running…" : "See the past";
  if (buttonId === "run-ahead") button.textContent = busy ? "Running…" : "Show a range";
  if (buttonId === "run-last") button.textContent = busy ? "Running…" : "Check the past";
  const sample = document.getElementById("run-sample");
  if (sample) sample.disabled = busy;
}

function showError(message) {
  problems[tab] = message;
  if (results[tab]) renderOutput();
  else resultsEl.innerHTML = `<p class="error" id="error">${esc(message)}</p>`;
}

function renderOutput() {
  const data = results[tab];
  if (!data) {
    resultsEl.innerHTML = placeholder();
    return;
  }
  if (tab === "back") renderBackResult(data);
  if (tab === "ahead") renderAheadResult(data);
  if (tab === "last") renderLastResult(data);
}

function placeholder() {
  const copy = {
    back: "The chart shows what those deposits would have been worth. The preset saves $500 a month, then $1,500, and shifts the mix halfway through 30 years.",
    ahead: "Pick a mix and an amount. The chart draws a lucky path, a typical path, and a rough path by reshuffling that mix's past years.",
    last: "Start with a balance and a yearly amount to spend. The count is how often that spending would have lasted through real past stretches.",
  };
  const error = problems[tab] ? `<p class="error" id="error">${esc(problems[tab])}</p>` : "";
  return `<section class="placeholder"><h2>${esc(TITLES[tab])}</h2>${error}<p>${esc(copy[tab])}</p></section>`;
}

function renderBackResult(data) {
  const today = state.back.todayDollars;
  const stats = data.stats;
  const ending = today ? stats.real_ending_value : stats.ending_value;
  const added = today ? stats.real_net_contributions : stats.net_contributions;
  const gain = today ? stats.real_market_gain : stats.market_gain;
  const drop = stats.max_drawdown == null
    ? ""
    : `The biggest drop was ${percent(stats.max_drawdown, stats.max_drawdown >= 0.1 ? 0 : 1)}, from ${monthYear(stats.drawdown_peak)} to ${monthYear(stats.drawdown_trough)}. That drop is in the dollars of the time.`;
  const error = problems.back ? `<p class="error" id="error">${esc(problems.back)}</p>` : `<div id="error"></div>`;
  resultsEl.innerHTML = `
    ${data.source === "sample" ? `<p class="banner">Sample prices, not market history. The shape is a seeded random walk so you can try the controls offline.</p>` : `<p class="banner">Market prices: closes, cash dividends, and splits. What you type stays in this browser.</p>`}
    ${error}
    <section class="panel">
      <h2>What it would have been worth</h2>
      <p class="caption">${esc(data.caption)}</p>
      <label class="check"><input id="today-dollars" type="checkbox" ${today ? "checked" : ""}> Show amounts in today's dollars</label>
      <div class="stats">
        ${stat(today ? "Ending value in today's dollars" : "Ending value", money.format(ending))}
        ${stat(today ? "Money you added, in today's dollars" : "Money you added", money.format(added))}
        ${stat(today ? "Change after the cost of living" : "Change from prices", money.format(gain), gain >= 0 ? "up" : "down")}
        ${stat("Your return, counting when you added money", percent(stats.xirr))}
        ${stat("The mix's pace, ignoring when you added money", percent(stats.twr_cagr))}
        ${stat("Left uninvested", money.format(stats.skipped))}
      </div>
      ${drop ? `<p class="caption">${esc(drop)}</p>` : ""}
      <p class="caption">The two return rates stay in the dollars of the time. They are not adjusted for the cost of living.</p>
      <div class="chart-host" id="history-chart"></div>
      <ul class="legend" id="history-legend"></ul>
      ${data.notes.length ? `<ul class="notes">${data.notes.map((note) => `<li>${esc(note)}</li>`).join("")}</ul>` : ""}
      ${data.benchmark?.error ? `<p class="quote bad">The comparison fund could not be priced. ${esc(data.benchmark.error)}</p>` : ""}
      <h3 style="margin-top:18px">Holdings at the end</h3>
      <div class="table-scroll"><table>
        <thead>
          <tr>
            <th>Symbol</th><th>Prices from</th><th class="num">Value</th><th class="num">Shares</th>
            <th class="num">Dividends reinvested</th><th class="num">Dividends paid in cash</th><th class="num">Not invested</th>
          </tr>
        </thead>
        <tbody>
          ${data.sleeves.map((sleeve) => `
            <tr>
              <td>${esc(sleeve.symbol)}</td>
              <td>${esc(sleeve.listed_on ? monthYear(sleeve.listed_on) : "—")}</td>
              <td class="num">${money.format(sleeve.value)}</td>
              <td class="num">${sleeve.shares.toLocaleString("en-US", { maximumFractionDigits: 2 })}</td>
              <td class="num">${money.format(sleeve.dividends_reinvested)}</td>
              <td class="num">${money.format(sleeve.dividends_cash)}</td>
              <td class="num">${money.format(sleeve.skipped)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table></div>
      <div class="buttons" style="margin-top:14px">
        <button type="button" data-action="use-ending">See how long this ending amount lasts</button>
      </div>
    </section>
  `;
  renderHistory(data);
}

function renderAheadResult(data) {
  const fan = data.montecarlo;
  const error = problems.ahead ? `<p class="error" id="error">${esc(problems.ahead)}</p>` : "";
  const success = fan && fan.success_rate != null
    ? `<div class="stats">${stat("Share of paths with money left", `${(fan.success_rate * 100).toFixed(0)}%`, fan.success_rate >= 0.5 ? "up" : "down")}</div>`
    : "";
  resultsEl.innerHTML = `
    ${error}
    <section class="panel">
      <h2>${esc(data.mix_label)}</h2>
      <p class="caption">${esc(data.detail)}. ${esc(data.plain)}</p>
      ${fan ? `${success}<div class="chart-host" id="forward-chart"></div><ul class="legend" id="forward-legend"></ul>` : ""}
    </section>
  `;
  if (!fan) return;
  drawChart(document.getElementById("forward-chart"), document.getElementById("forward-legend"), {
    categories: fan.years.map((year) => (year === 0 ? "Now" : `Year ${year}`)),
    series: [
      { name: "Lucky path", values: fan.p90, color: "#245f8a", width: 1.5 },
      { name: "Typical path", values: fan.p50, color: "#1c1915", width: 2.5 },
      { name: "Rough path", values: fan.p10, color: "#8c4a2f", width: 1.5 },
      { name: "Money in, minus spending", values: fan.deposited, color: "#8a8175", dash: "5 4", width: 1.5 },
    ],
    aria: "Lucky, typical, and rough paths by year",
  });
}

function renderLastResult(data) {
  const error = problems.last ? `<p class="error" id="error">${esc(problems.last)}</p>` : "";
  const summary = data.unavailable
    ? `<p>${esc(data.unavailable)}</p>`
    : `
      <div class="stats">
        ${stat("Periods with money left", `${data.lasted} of ${data.tested}`, data.lasted / data.tested >= 0.5 ? "up" : "down")}
        ${stat("Typical amount left", money.format(data.median_ending))}
        ${stat("Shortest run", data.shortest === data.horizon ? "Lasted" : `Year ${data.shortest + 1}`)}
      </div>
      <div class="chart-host" id="lasting-chart"></div>
      <ul class="legend" id="lasting-legend"></ul>
    `;
  resultsEl.innerHTML = `
    ${error}
    <section class="panel">
      <h2>${esc(data.mix_label)}</h2>
      <p class="caption">${esc(data.detail)}. ${esc(data.plain || "")}</p>
      ${summary}
    </section>
  `;
  if (!data.periods?.length) return;
  drawChart(document.getElementById("lasting-chart"), document.getElementById("lasting-legend"), {
    categories: data.periods.map((period) => String(period.start)),
    series: [
      { name: "Money left", values: data.periods.map((period) => period.ending), color: "#1c1915", width: 2 },
    ],
    aria: "Money left at the end of each historical period",
  });
}

function renderHistory(data) {
  const history = document.getElementById("history-chart");
  if (!history) return;
  const points = data.points;
  if (!points.length) {
    history.innerHTML = "<p>No prices landed inside this window.</p>";
    return;
  }
  const today = state.back.todayDollars;
  const symbols = Object.keys(points[0].sleeves);
  const valueOf = (point) => (today ? point.real_value : point.value);
  const addedOf = (point) => (today ? point.real_net_contributions : point.net_contributions);
  const series = [
    { name: "Portfolio value", values: points.map(valueOf), color: "#1c1915", width: 2.5 },
    { name: "Money you added", values: points.map(addedOf), color: "#8a8175", dash: "5 4", width: 1.5 },
  ];
  if (data.benchmark && !data.benchmark.error && data.benchmark.values?.length) {
    const values = today ? data.benchmark.real_values : data.benchmark.values;
    series.push({
      name: `Same deposits in ${data.benchmark.symbol}`,
      values,
      color: "#245f8a",
      dash: "2 3",
      width: 1.5,
    });
  }
  symbols.forEach((symbol, index) => {
    series.push({
      name: symbol,
      values: points.map((point) => {
        const nominal = point.sleeves[symbol] || 0;
        if (!today || !point.value) return nominal;
        return nominal * point.real_value / point.value;
      }),
      color: PALETTE[index % PALETTE.length],
      width: 1.25,
    });
  });
  drawChart(history, document.getElementById("history-legend"), {
    categories: points.map((point) => point.date),
    series,
    aria: `Portfolio value from ${points[0].date} to ${points[points.length - 1].date}`,
  });
}

function drawChart(host, legend, model) {
  if (!host || !legend) return;
  const width = Math.max(host.clientWidth || 640, 320);
  const height = 340;
  const pad = { l: 76, r: 16, t: 16, b: 36 };
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const flat = model.series.flatMap((item) => item.values.filter((value) => value != null && !Number.isNaN(value)));
  const min = Math.min(0, ...flat);
  const max = Math.max(...flat, 1);
  const span = max - min || 1;
  const n = model.categories.length;
  const xAt = (index) => pad.l + (n === 1 ? innerW / 2 : (index / (n - 1)) * innerW);
  const yAt = (value) => pad.t + ((max - value) / span) * innerH;
  const grid = [];
  const labels = [];
  for (let tick = 0; tick <= 4; tick += 1) {
    const value = min + (span * tick) / 4;
    const y = yAt(value);
    grid.push(`<line x1="${pad.l}" y1="${y}" x2="${width - pad.r}" y2="${y}" stroke="#ddd4c6" stroke-width="1"></line>`);
    labels.push(`<text x="${pad.l - 8}" y="${y + 4}" text-anchor="end" font-size="11" fill="#6f675e">${esc(compactMoney(value))}</text>`);
  }
  const xLabels = [];
  const markCount = Math.min(6, n);
  for (let mark = 0; mark < markCount; mark += 1) {
    const index = markCount === 1 ? 0 : Math.round((mark * (n - 1)) / (markCount - 1));
    xLabels.push(`<text x="${xAt(index)}" y="${height - 12}" text-anchor="middle" font-size="11" fill="#6f675e">${esc(model.categories[index])}</text>`);
  }
  const paths = model.series.map((item) => {
    let drawing = false;
    const commands = [];
    item.values.forEach((value, index) => {
      if (value == null || Number.isNaN(value)) {
        drawing = false;
        return;
      }
      commands.push(`${drawing ? "L" : "M"} ${xAt(index).toFixed(2)} ${yAt(value).toFixed(2)}`);
      drawing = true;
    });
    const dash = item.dash ? ` stroke-dasharray="${item.dash}"` : "";
    return `<path d="${commands.join(" ")}" fill="none" stroke="${item.color}" stroke-width="${item.width}"${dash}></path>`;
  }).join("");
  host.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(model.aria)}">
      ${grid.join("")}
      ${labels.join("")}
      ${xLabels.join("")}
      ${paths}
    </svg>
    <div class="tooltip" hidden></div>
  `;
  legend.innerHTML = model.series.map((item) => `
    <li><span class="swatch" style="background:${item.color}${item.dash ? "; height:0; border-top:2px dashed " + item.color : ""}"></span>${esc(item.name)}</li>
  `).join("");
  const svg = host.querySelector("svg");
  const tip = host.querySelector(".tooltip");
  svg.addEventListener("mousemove", (event) => {
    const rect = svg.getBoundingClientRect();
    const scale = width / rect.width;
    const x = (event.clientX - rect.left) * scale - pad.l;
    let index = n === 1 ? 0 : Math.round((x / innerW) * (n - 1));
    index = Math.max(0, Math.min(n - 1, index));
    const lines = [`<strong>${esc(model.categories[index])}</strong>`].concat(
      model.series.map((item) => {
        const value = item.values[index];
        return `${esc(item.name)}: ${value == null ? "—" : money.format(value)}`;
      }),
    );
    tip.innerHTML = lines.join("<br>");
    tip.hidden = false;
    tip.style.left = `${Math.min(event.clientX - rect.left + 12, rect.width - 180)}px`;
    tip.style.top = `${event.clientY - rect.top + 12}px`;
  });
  svg.addEventListener("mouseleave", () => {
    tip.hidden = true;
  });
}

function refreshQuotes() {
  const symbols = state.back.sleeves.map((sleeve) => sleeve.symbol).filter(Boolean);
  if (state.back.compare && state.back.benchmark) symbols.push(state.back.benchmark);
  Promise.all(symbols.map(fetchQuote)).then(paintQuotes);
}

function fetchQuote(symbol) {
  if (!quoteJobs[symbol]) {
    quoteJobs[symbol] = fetch(`/api/symbol/${encodeURIComponent(symbol)}`)
      .then(async (response) => {
        const data = await response.json().catch(() => null);
        quotes[symbol] = response.ok ? data : { error: errorText(data) };
        return quotes[symbol];
      })
      .catch(() => {
        quotes[symbol] = { error: "Could not check that symbol." };
        return quotes[symbol];
      });
  }
  return quoteJobs[symbol];
}

function paintQuotes() {
  document.querySelectorAll(".quote").forEach((node) => {
    const symbol = node.dataset.symbol;
    if (!symbol) {
      node.textContent = "";
      return;
    }
    const quote = quotes[symbol];
    if (!quote) {
      node.textContent = "Checking prices…";
      node.className = "quote";
      return;
    }
    if (quote.error) {
      node.textContent = quote.error;
      node.className = "quote bad";
      return;
    }
    const listed = quote.listed_on;
    let text = `Prices start ${monthYear(listed)}.`;
    let bad = false;
    if (listed && state.back.end && state.back.end < listed) {
      text = `Prices start ${monthYear(listed)}, after this chart ends. Nothing in this window can be invested.`;
      bad = true;
    } else if (listed && state.back.start && state.back.start < listed) {
      text = `Prices start ${monthYear(listed)}. Earlier amounts are not invested.`;
    }
    if (quote.delisted_on) text += ` Last price ${monthYear(quote.delisted_on)}.`;
    node.textContent = text;
    node.className = bad ? "quote bad" : "quote";
  });
}

function stat(label, value, tone) {
  return `<p class="stat ${tone || ""}"><b>${esc(value)}</b><span>${esc(label)}</span></p>`;
}

function percent(value, digits = 2) {
  if (value == null) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

function compactMoney(value) {
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 10_000) return `${sign}$${Math.round(abs / 1000)}k`;
  return `${sign}$${Math.round(abs)}`;
}

function monthYear(iso) {
  if (!iso) return "";
  const [year, month] = iso.split("-");
  return `${MONTHS[Number(month) - 1] || ""} ${year}`.trim();
}

function isoDate(value) {
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

function parseIso(iso) {
  return new Date(`${iso}T00:00:00`);
}

function addYears(iso, years) {
  const day = parseIso(iso);
  day.setFullYear(day.getFullYear() + years);
  return isoDate(day);
}

function addDays(iso, days) {
  const day = parseIso(iso);
  day.setDate(day.getDate() + days);
  return isoDate(day);
}

function cadenceLabel(frequency) {
  return CADENCE[frequency] || "Each month";
}

function twoStretches(startIso, endIso) {
  const mid = addYears(startIso, 15);
  if (mid >= endIso) return [stretch(startIso, endIso, 1000, [60, 40])];
  return [
    stretch(startIso, addDays(mid, -1), 500, [70, 30]),
    stretch(mid, endIso, 1500, [60, 40]),
  ];
}

function stretch(start, end, amount, percents) {
  return {
    start,
    end,
    frequency: "monthly",
    mode: "percent",
    amount,
    percents: percents.slice(),
    dollars: percents.map((pct) => Math.round(amount * pct / 100)),
  };
}

function errorText(payload) {
  if (!payload) return "That request failed.";
  if (typeof payload.detail === "string") return payload.detail;
  if (Array.isArray(payload.detail)) return payload.detail.map((item) => item.msg || String(item)).join(" ");
  return "That request failed.";
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}
