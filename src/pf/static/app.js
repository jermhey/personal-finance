const PALETTE = ["#1f6b4a", "#8c4a2f", "#245f8a", "#6e5a28", "#3e6b62", "#7a3e55"];
const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const CADENCE = {
  once: "One time",
  weekly: "Each week",
  biweekly: "Every two weeks",
  monthly: "Each month",
  quarterly: "Each quarter",
  yearly: "Each year",
};

const state = defaultState();
let lastPayload = null;

const form = document.getElementById("controls");
form.addEventListener("submit", (event) => {
  event.preventDefault();
  run(false);
});
form.addEventListener("click", (event) => {
  const action = event.target.dataset.action;
  if (!action) return;
  event.preventDefault();
  capture();
  if (action === "add-sleeve") addSleeve();
  if (action === "remove-sleeve") {
    const index = Number(event.target.dataset.index);
    state.sleeves.splice(index, 1);
    for (const period of state.periods) {
      period.percents.splice(index, 1);
      period.dollars.splice(index, 1);
    }
  }
  if (action === "add-period") addPeriod();
  if (action === "add-lump") addLump();
  if (action === "remove-period") state.periods.splice(Number(event.target.dataset.index), 1);
  if (action === "set-mode") {
    const period = state.periods[Number(event.target.dataset.index)];
    const mode = event.target.dataset.mode;
    if (period && period.mode !== mode) convertPeriod(period, mode);
  }
  if (action === "preview") {
    render();
    run(true);
    return;
  }
  render();
  if (action === "add-sleeve") {
    const inputs = form.querySelectorAll(".symbol");
    inputs[inputs.length - 1].focus();
  }
});
form.addEventListener("input", (event) => {
  if (event.target.matches(".weight, .dollars, .period-amount")) refreshPeriodNotes();
  const err = document.getElementById("error");
  if (err && err.textContent) err.remove();
});
form.addEventListener("change", (event) => {
  if (event.target.id === "rebalance") {
    document.getElementById("threshold-wrap").hidden = event.target.value !== "threshold";
  }
  if (event.target.classList.contains("symbol")) {
    capture();
    render();
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
  if (event.target.classList.contains("period-start") || event.target.classList.contains("period-end")) {
    refreshPeriodNotes();
  }
  if (event.target.classList.contains("period-frequency")) {
    if (event.target.value === "once") {
      capture();
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

render();

function defaultState() {
  const end = new Date();
  const start = new Date();
  start.setFullYear(end.getFullYear() - 30);
  const startIso = isoDate(start);
  const endIso = isoDate(end);
  return {
    start: startIso,
    end: endIso,
    listing_policy: "skip",
    rebalance: "yearly",
    threshold: 5,
    sleeves: [
      { symbol: "VFINX", drip: true },
      { symbol: "VBMFX", drip: true },
    ],
    periods: twoStretches(startIso, endIso),
    forwardYears: 30,
    forwardContrib: 12000,
    forwardWithdraw: 0,
  };
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
  if (mid >= endIso) {
    return [stretch(startIso, endIso, 1000, [60, 40])];
  }
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

function render() {
  const thresholdHidden = state.rebalance !== "threshold" ? "hidden" : "";
  form.innerHTML = `
    <section class="block">
      <h2>Chart window</h2>
      <div class="row">
        <label>From<input id="start" type="date" value="${esc(state.start)}" required></label>
        <label>Through<input id="end" type="date" value="${esc(state.end)}" required></label>
      </div>
    </section>
    <section class="block">
      <h2>Holdings</h2>
      <p class="help">Type a symbol, then choose whether its dividends buy more shares.</p>
      ${state.sleeves.map((sleeve, index) => `
        <div class="sleeve">
          <div class="row">
            <label class="grow">Symbol<input class="symbol" value="${esc(sleeve.symbol)}" autocomplete="off" spellcheck="false"></label>
            <button type="button" class="ghost" data-action="remove-sleeve" data-index="${index}" ${state.sleeves.length === 1 ? "disabled" : ""}>Remove</button>
          </div>
          <label class="check"><input class="drip" type="checkbox" ${sleeve.drip ? "checked" : ""}> Reinvest dividends</label>
        </div>
      `).join("")}
      <button type="button" data-action="add-sleeve">Add holding</button>
    </section>
    <section class="block">
      <h2>What you added</h2>
      <p class="help">The account starts empty. A stretch adds income on a repeating schedule. A lump sum is invested once, on the date you choose, in addition to any stretch that covers that day.</p>
      ${state.periods.map((period, index) => periodCard(period, index)).join("")}
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
          ${options(["never", "monthly", "quarterly", "yearly", "threshold"], state.rebalance, {
            never: "Don't rebalance",
            monthly: "Every month",
            quarterly: "Every quarter",
            yearly: "Every year",
            threshold: "When a holding drifts",
          })}
        </select>
      </label>
      <label id="threshold-wrap" ${thresholdHidden}>Drift (percentage points)
        <input id="threshold" type="number" min="1" max="50" step="1" value="${esc(state.threshold)}">
      </label>
    </section>
    <section class="block">
      <h2>If this continued</h2>
      <p class="help">Draws from the annual returns of the chart, one year at a time.</p>
      <div class="row">
        <label>Years<input id="forward-years" type="number" min="1" max="60" value="${esc(state.forwardYears)}"></label>
        <label>Added each year<input id="forward-contrib" type="number" step="100" value="${esc(state.forwardContrib)}"></label>
      </div>
      <label>Withdrawn each year<input id="forward-withdraw" type="number" step="100" value="${esc(state.forwardWithdraw)}"></label>
    </section>
    <div class="buttons">
      <button class="primary" type="submit" id="run-market">Run historical</button>
      <button type="button" id="run-sample" data-action="preview">Preview sample prices</button>
    </div>
  `;
  refreshPeriodNotes();
}

function periodCard(period, index) {
  const lump = period.frequency === "once";
  const cadence = lump ? "Amount" : cadenceLabel(period.frequency);
  const stretchNumber = state.periods.slice(0, index + 1).filter((item) => item.frequency !== "once").length;
  const linkedStart = !lump && period.start === state.start && index === state.periods.findIndex((item) => item.frequency !== "once") ? "data-linked=\"start\"" : "";
  const lastRecurring = state.periods.reduce((found, item, itemIndex) => (item.frequency === "once" ? found : itemIndex), -1);
  const linkedEnd = !lump && index === lastRecurring && period.end === state.end ? "data-linked=\"end\"" : "";
  return `
    <article class="period${lump ? " lump" : ""}" data-mode="${period.mode}">
      <div class="period-head">
        <p class="period-title">${lump ? "Lump sum" : `Stretch ${stretchNumber}`}</p>
        <button type="button" class="ghost" data-action="remove-period" data-index="${index}" ${state.periods.length === 1 ? "disabled" : ""}>Remove</button>
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
      <div class="toggle" role="group" aria-label="How to enter stretch ${index + 1}">
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
  return state.sleeves.map((sleeve, index) => `
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

function capture() {
  if (!form.querySelector(".sleeve")) return;
  state.start = form.querySelector("#start").value;
  state.end = form.querySelector("#end").value;
  state.listing_policy = "skip";
  state.rebalance = form.querySelector("#rebalance").value;
  state.threshold = Number(form.querySelector("#threshold").value);
  state.forwardYears = Number(form.querySelector("#forward-years").value);
  state.forwardContrib = Number(form.querySelector("#forward-contrib").value);
  state.forwardWithdraw = Number(form.querySelector("#forward-withdraw").value);
  state.sleeves = [...form.querySelectorAll(".sleeve")].map((row) => ({
    symbol: row.querySelector(".symbol").value.trim().toUpperCase(),
    drip: row.querySelector(".drip").checked,
  }));
  state.periods = [...form.querySelectorAll(".period")].map((row) => ({
    start: row.querySelector(".period-start").value,
    end: row.querySelector(".period-end").value,
    frequency: row.querySelector(".period-frequency").value,
    mode: row.dataset.mode,
    amount: Number(row.querySelector(".period-amount").value),
    percents: [...row.querySelectorAll(".weight")].map((input) => Number(input.value)),
    dollars: [...row.querySelectorAll(".dollars")].map((input) => Number(input.value)),
  }));
}

function addSleeve() {
  state.sleeves.push({ symbol: "", drip: true });
  for (const period of state.periods) {
    period.percents.push(0);
    period.dollars.push(0);
  }
}

function addPeriod() {
  const recurring = state.periods.filter((period) => period.frequency !== "once");
  const last = recurring[recurring.length - 1] || state.periods[state.periods.length - 1];
  let start = addYears(last.start, 10);
  if (start >= state.end) start = addDays(last.start, 1);
  if (start >= state.end) start = state.end;
  const previousEnd = addDays(start, -1);
  if (last.frequency !== "once" && last.end >= start && previousEnd >= last.start) last.end = previousEnd;
  const scale = 2;
  state.periods.push({
    start,
    end: state.end,
    frequency: "monthly",
    mode: last.mode,
    amount: Math.round((Number(last.amount) || 0) * scale),
    percents: last.percents.slice(),
    dollars: last.dollars.map((value) => Math.round((Number(value) || 0) * scale)),
  });
  sortPeriods();
}

function addLump() {
  const template = state.periods.find((period) => period.frequency !== "once") || state.periods[0];
  let start = addYears(state.start, 5);
  if (!start || start < state.start) start = state.start;
  if (start > state.end) start = state.end;
  const amount = 10000;
  const fractions = mixFractions(template);
  state.periods.push({
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
  state.periods.sort((a, b) => a.start.localeCompare(b.start) || (a.frequency === "once") - (b.frequency === "once"));
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
  capture();
  const symbols = state.sleeves.map((sleeve) => sleeve.symbol);
  const regimes = [];
  const flows = [];
  for (const period of state.periods) {
    const end = period.frequency === "once" ? null : period.end || null;
    const { weights, amount, withdrawals } = splitPeriod(period, symbols);
    const invested = Object.values(weights).reduce((sum, value) => sum + value, 0);
    if (period.frequency !== "once" && period.start && invested > 0) {
      regimes.push({ date: period.start, weights });
    }
    if (amount) {
      flows.push({
        amount,
        frequency: period.frequency,
        start: period.start,
        end,
        allocation: weights,
      });
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
    start: state.start,
    end: state.end,
    listing_policy: state.listing_policy,
    rebalance: { mode: state.rebalance, threshold: Number(state.threshold) / 100 },
    sleeves: state.sleeves.map((sleeve) => ({ symbol: sleeve.symbol, drip: sleeve.drip })),
    regimes,
    flows,
    forward: {
      years: Number(state.forwardYears),
      annual_contribution: Number(state.forwardContrib),
      annual_withdrawal: Number(state.forwardWithdraw),
      trials: 500,
    },
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

function validate(body) {
  if (!body.start || !body.end) return "Choose a start and an end date.";
  if (body.end < body.start) return "The end date is before the start date.";
  if (!body.sleeves.length) return "Add at least one holding.";
  if (body.sleeves.some((sleeve) => !sleeve.symbol)) return "Enter a symbol for each holding.";
  const unique = new Set(body.sleeves.map((sleeve) => sleeve.symbol));
  if (unique.size !== body.sleeves.length) return "Each holding can appear once.";
  if (!state.periods.length) return "Add at least one stretch or lump sum.";
  const starts = new Set();
  for (const period of state.periods) {
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
  const ahead = body.forward.years;
  if (!(ahead >= 1 && ahead <= 60)) return "Looking ahead must be between 1 and 60 years.";
  if (body.forward.annual_contribution < 0 || body.forward.annual_withdrawal < 0) {
    return "The forward contribution and withdrawal are entered as positive amounts. A minus belongs on a stretch.";
  }
  if (body.rebalance.mode === "threshold" && !(body.rebalance.threshold > 0 && body.rebalance.threshold < 1)) {
    return "Drift must be between 1 and 99 percentage points.";
  }
  if (!body.flows.length) return "Enter an amount for at least one stretch or lump sum.";
  return "";
}

async function run(sample) {
  const body = payload();
  const problem = validate(body);
  if (problem) {
    showError(problem);
    return;
  }
  setBusy(true);
  clearError();
  try {
    const response = await fetch(sample ? "/api/preview" : "/api/backtest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) {
      showError(errorText(data));
      return;
    }
    lastPayload = data;
    renderOutput(data);
  } catch (_error) {
    showError("The server did not respond.");
  } finally {
    setBusy(false);
  }
}

function setBusy(busy) {
  for (const id of ["run-market", "run-sample"]) {
    const button = document.getElementById(id);
    if (button) button.disabled = busy;
  }
  const primary = document.getElementById("run-market");
  if (primary) primary.textContent = busy ? "Running…" : "Run historical";
}

function renderOutput(data) {
  document.getElementById("placeholder").hidden = true;
  const output = document.getElementById("output");
  output.hidden = false;
  const stats = data.stats;
  const mc = data.montecarlo;
  output.innerHTML = `
    ${data.source === "sample" ? `<p class="banner">Sample prices, not market history. The shape is a seeded random walk so you can try the controls offline.</p>` : `<p class="banner">Market prices: closes, cash dividends, and splits, cached on this machine.</p>`}
    <div id="error"></div>
    <section class="panel">
      <h2>Portfolio value</h2>
      <p class="caption">${esc(data.caption)}</p>
      <div class="stats">
        ${stat("Ending value", money.format(stats.ending_value))}
        ${stat("Income added", money.format(stats.net_contributions))}
        ${stat("Market gain", money.format(stats.market_gain), stats.market_gain >= 0 ? "up" : "down")}
        ${stat("Personal IRR", percent(stats.xirr))}
        ${stat("Time-weighted return", percent(stats.twr_cagr))}
        ${stat("Skipped deposits", money.format(stats.skipped))}
      </div>
      <div class="chart-host" id="history-chart"></div>
      <ul class="legend" id="history-legend"></ul>
      ${data.notes.length ? `<ul class="notes">${data.notes.map((note) => `<li>${esc(note)}</li>`).join("")}</ul>` : ""}
      <h3 style="margin-top:18px">Holdings at the end</h3>
      <div class="table-scroll"><table>
        <thead>
          <tr>
            <th>Symbol</th><th>Priced from</th><th class="num">Value</th><th class="num">Shares</th>
            <th class="num">Reinvested</th><th class="num">Cash dividends</th><th class="num">Skipped</th>
          </tr>
        </thead>
        <tbody>
          ${data.sleeves.map((sleeve) => `
            <tr>
              <td>${esc(sleeve.symbol)}</td>
              <td>${esc(sleeve.listed_on || "—")}</td>
              <td class="num">${money.format(sleeve.value)}</td>
              <td class="num">${sleeve.shares.toLocaleString("en-US", { maximumFractionDigits: 2 })}</td>
              <td class="num">${money.format(sleeve.dividends_reinvested)}</td>
              <td class="num">${money.format(sleeve.dividends_cash)}</td>
              <td class="num">${money.format(sleeve.skipped)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table></div>
    </section>
    <section class="panel forward">
      <h2>Forward projection</h2>
      ${mc.unavailable ? `<p>${esc(mc.unavailable)}</p>` : `
        <p class="caption">${esc(mc.method)}</p>
        ${mc.success_rate == null ? "" : `<div class="stats">${stat("Chance the balance stays positive", (mc.success_rate * 100).toFixed(1) + "%", mc.success_rate >= 0.5 ? "up" : "down")}</div>`}
        <div class="chart-host" id="forward-chart"></div>
        <ul class="legend" id="forward-legend"></ul>
      `}
    </section>
  `;
  renderCharts(data);
}

function renderCharts(data) {
  const history = document.getElementById("history-chart");
  if (!history) return;
  const points = data.points;
  if (!points.length) {
    history.innerHTML = "<p>No prices landed inside this window.</p>";
    return;
  }
  const symbols = Object.keys(points[0].sleeves);
  const series = [
    { name: "Portfolio value", values: points.map((point) => point.value), color: "#1c1915", width: 2.5 },
    { name: "Income added", values: points.map((point) => point.net_contributions), color: "#8a8175", dash: "5 4", width: 1.5 },
  ];
  symbols.forEach((symbol, index) => {
    series.push({
      name: symbol,
      values: points.map((point) => point.sleeves[symbol] || 0),
      color: PALETTE[index % PALETTE.length],
      width: 1.25,
    });
  });
  drawChart(history, document.getElementById("history-legend"), {
    categories: points.map((point) => point.date),
    series,
    aria: `Portfolio value from ${points[0].date} to ${points[points.length - 1].date}`,
  });

  const mc = data.montecarlo;
  const forward = document.getElementById("forward-chart");
  if (!forward || !mc.p50) return;
  drawChart(forward, document.getElementById("forward-legend"), {
    categories: mc.years.map((year) => (year === 0 ? "Now" : `Year ${year}`)),
    series: [
      { name: "90th percentile", values: mc.p90, color: "#245f8a", width: 1.5 },
      { name: "Median", values: mc.p50, color: "#1c1915", width: 2.5 },
      { name: "10th percentile", values: mc.p10, color: "#8c4a2f", width: 1.5 },
      { name: "Net deposits", values: mc.deposited, color: "#8a8175", dash: "5 4", width: 1.5 },
    ],
    aria: "Forward balance percentiles by year",
  });
}

function drawChart(host, legend, model) {
  const width = Math.max(host.clientWidth || 640, 320);
  const height = 340;
  const pad = { l: 76, r: 16, t: 16, b: 36 };
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const flat = model.series.flatMap((item) => item.values);
  const min = Math.min(0, ...flat);
  const max = Math.max(...flat, 1);
  const span = max - min || 1;
  const n = model.categories.length;
  const xAt = (index) => pad.l + (n === 1 ? innerW / 2 : (index / (n - 1)) * innerW);
  const yAt = (value) => pad.t + ((max - value) / span) * innerH;

  const ticks = 4;
  const grid = [];
  const labels = [];
  for (let tick = 0; tick <= ticks; tick += 1) {
    const value = min + (span * tick) / ticks;
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
    const d = item.values.map((value, index) => `${index === 0 ? "M" : "L"} ${xAt(index).toFixed(2)} ${yAt(value).toFixed(2)}`).join(" ");
    const dash = item.dash ? ` stroke-dasharray="${item.dash}"` : "";
    return `<path d="${d}" fill="none" stroke="${item.color}" stroke-width="${item.width}"${dash}></path>`;
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
    <li><span class="swatch" style="background:${item.color}"></span>${esc(item.name)}</li>
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
      model.series.map((item) => `${esc(item.name)}: ${money.format(item.values[index])}`),
    );
    tip.innerHTML = lines.join("<br>");
    tip.hidden = false;
    const left = event.clientX - rect.left + 12;
    tip.style.left = `${Math.min(left, rect.width - 180)}px`;
    tip.style.top = `${event.clientY - rect.top + 12}px`;
  });
  svg.addEventListener("mouseleave", () => {
    tip.hidden = true;
  });
}

function stat(label, value, tone) {
  return `<p class="stat ${tone || ""}"><b>${esc(value)}</b><span>${esc(label)}</span></p>`;
}

function percent(value) {
  if (value == null) return "—";
  return `${(value * 100).toFixed(2)}%`;
}

function compactMoney(value) {
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 10_000) return `${sign}$${Math.round(abs / 1000)}k`;
  return `${sign}$${Math.round(abs)}`;
}

function showError(message) {
  document.getElementById("placeholder").hidden = true;
  const output = document.getElementById("output");
  output.hidden = false;
  output.innerHTML = `<p class="error" id="error">${esc(message)}</p>`;
}

function clearError() {
  const existing = document.getElementById("error");
  if (existing) existing.remove();
}

function errorText(payload) {
  if (!payload) return "The backtest failed.";
  if (typeof payload.detail === "string") return payload.detail;
  if (Array.isArray(payload.detail)) {
    return payload.detail.map((item) => item.msg || String(item)).join(" ");
  }
  return "The backtest failed.";
}

function esc(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

window.addEventListener("resize", () => {
  if (lastPayload) renderCharts(lastPayload);
});
