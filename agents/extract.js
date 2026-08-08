// Runs inside the page: pulls out QA-relevant elements with stable selectors.
() => {
  const MAX = 120;

  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
  };

  const nameOf = (el) => {
    const label = el.labels && el.labels[0] ? el.labels[0].innerText : "";
    return (
      el.getAttribute("aria-label") ||
      label ||
      el.getAttribute("placeholder") ||
      (el.innerText || "").trim() ||
      el.value ||
      el.getAttribute("title") ||
      el.name ||
      ""
    ).trim().replace(/\s+/g, " ").slice(0, 80);
  };

  const path = (el) => {
    const parts = [];
    while (el && el.nodeType === 1 && el.tagName !== "BODY" && parts.length < 5) {
      const tag = el.tagName.toLowerCase();
      const sibs = [...el.parentElement.children].filter((c) => c.tagName === el.tagName);
      parts.unshift(sibs.length > 1 ? `${tag}:nth-of-type(${sibs.indexOf(el) + 1})` : tag);
      el = el.parentElement;
    }
    return parts.join(" > ");
  };

  const TEST_ATTRS = ["data-testid", "data-test-id", "data-test", "data-cy", "data-qa"];

  // A selector is only worth emitting if it resolves back to this exact element.
  const resolves = (el, sel) => {
    try {
      const hits = document.querySelectorAll(sel);
      return hits.length === 1 && hits[0] === el;
    } catch {
      return false;
    }
  };

  const selector = (el) => {
    const candidates = [];
    for (const attr of TEST_ATTRS) {
      const v = el.getAttribute(attr);
      if (v) candidates.push(`[${attr}="${v}"]`); // keep the attribute the app really uses
    }
    if (el.id && !/^\d/.test(el.id)) candidates.push(`#${CSS.escape(el.id)}`);
    if (el.name) candidates.push(`${el.tagName.toLowerCase()}[name="${el.name}"]`);
    candidates.push(path(el));
    return candidates.find((c) => resolves(el, c)) || "";
  };

  const kindOf = (el) => {
    const tag = el.tagName.toLowerCase();
    if (tag === "a") return "link";
    if (tag === "button") return "button";
    if (tag === "select") return "select";
    if (tag === "textarea") return "textarea";
    if (tag === "input") {
      const t = (el.type || "text").toLowerCase();
      return ["submit", "button", "reset"].includes(t) ? "button" : "input";
    }
    return el.getAttribute("role") === "link" ? "link" : "button";
  };

  // Ids like "product-01KZFREQ0RYPDAJVY1VRCXJCNA" are regenerated on every reseed.
  const looksGenerated = (v) =>
    /[A-Za-z0-9]{16,}/.test(v) && /\d/.test(v) && /[A-Za-z]/.test(v);

  const isVolatile = (el) => {
    for (const attr of TEST_ATTRS) {
      const v = el.getAttribute(attr);
      if (v && looksGenerated(v)) return true;
    }
    return !!(el.id && looksGenerated(el.id));
  };

  // Bootstrap-style wrappers disable a control without a disabled attribute on it.
  const isDisabled = (el) =>
    !!(el.disabled ||
      el.getAttribute("aria-disabled") === "true" ||
      el.closest('.disabled, [disabled], [aria-disabled="true"]'));

  const describe = (el) => ({
    kind: kindOf(el),
    label: nameOf(el),
    css: selector(el),
    role: el.getAttribute("role") || "",
    input_type: el.tagName.toLowerCase() === "input" ? (el.type || "text") : "",
    required: !!el.required,
    disabled: isDisabled(el),
    volatile: isVolatile(el),
    href: el.tagName.toLowerCase() === "a" ? el.href : "",
  });

  const SEL =
    'a[href], button, input, select, textarea, [role="button"], [role="link"], [role="tab"]';
  const nodes = [...document.querySelectorAll(SEL)].filter((el) => visible(el) && selector(el));

  const forms = [...document.querySelectorAll("form")].map((f) => {
    const fields = [...f.querySelectorAll("input, select, textarea")]
      .filter((el) => visible(el) && kindOf(el) !== "button")
      .map(describe);
    const submitEl = f.querySelector('button[type="submit"], input[type="submit"], button');
    return {
      name: f.getAttribute("name") || f.getAttribute("id") || nameOf(f).slice(0, 40) || "form",
      css: selector(f),
      fields,
      submit: submitEl ? describe(submitEl) : null,
    };
  });

  const origin = location.origin;
  const links = [...new Set([...document.querySelectorAll("a[href]")]
    .map((a) => a.href.split("#")[0])
    .filter((h) => h.startsWith(origin) && !/\.(pdf|zip|png|jpe?g|svg)$/i.test(h)))];

  return {
    url: location.href,
    title: document.title,
    headings: [...document.querySelectorAll("h1, h2")].map((h) => h.innerText.trim()).slice(0, 10),
    elements: nodes.slice(0, MAX).map(describe),
    forms,
    links: links.slice(0, 30),
  };
}
