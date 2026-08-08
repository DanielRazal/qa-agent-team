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

  const selector = (el) => {
    const tid =
      el.getAttribute("data-testid") || el.getAttribute("data-test") || el.getAttribute("data-cy");
    if (tid) return `[data-testid="${tid}"]`;
    if (el.id && !/^\d/.test(el.id)) return `#${CSS.escape(el.id)}`;
    if (el.name) return `${el.tagName.toLowerCase()}[name="${el.name}"]`;
    return path(el);
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

  const describe = (el) => ({
    kind: kindOf(el),
    label: nameOf(el),
    css: selector(el),
    role: el.getAttribute("role") || "",
    input_type: el.tagName.toLowerCase() === "input" ? (el.type || "text") : "",
    required: !!el.required,
    href: el.tagName.toLowerCase() === "a" ? el.href : "",
  });

  const SEL =
    'a[href], button, input, select, textarea, [role="button"], [role="link"], [role="tab"]';
  const nodes = [...document.querySelectorAll(SEL)].filter(visible);

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
