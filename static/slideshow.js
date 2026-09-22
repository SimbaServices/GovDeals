(function () {
  const root = document.getElementById("slideshow");
  if (!root) return;

  const image = root.querySelector("[data-slideshow-image]");
  const caption = root.querySelector("[data-slideshow-caption]");
  const title = root.querySelector("[data-slideshow-heading]");
  const prev = root.querySelector("[data-slideshow-prev]");
  const next = root.querySelector("[data-slideshow-next]");
  let urls = [];
  let index = 0;
  let lastFocus = null;

  function render() {
    if (!urls.length) return;
    image.src = urls[index];
    image.alt = title.textContent ? title.textContent + " photo " + (index + 1) : "Listing photo";
    caption.textContent = index + 1 + " / " + urls.length;
    const many = urls.length > 1;
    prev.hidden = !many;
    next.hidden = !many;
  }

  function open(nextUrls, heading) {
    urls = nextUrls;
    index = 0;
    lastFocus = document.activeElement;
    title.textContent = heading || "";
    root.hidden = false;
    document.body.classList.add("slideshow-open");
    render();
    root.querySelector(".slideshow-close").focus();
  }

  function close() {
    if (root.hidden) return;
    root.hidden = true;
    document.body.classList.remove("slideshow-open");
    image.removeAttribute("src");
    urls = [];
    if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
  }

  function step(delta) {
    if (!urls.length) return;
    index = (index + delta + urls.length) % urls.length;
    render();
  }

  function parseUrls(value) {
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed.filter(Boolean) : [];
    } catch (err) {
      return [];
    }
  }

  document.addEventListener("click", function (event) {
    const trigger = event.target.closest("[data-slideshow]");
    if (trigger) {
      event.preventDefault();
      const nextUrls = parseUrls(trigger.getAttribute("data-slideshow") || "[]");
      if (!nextUrls.length) return;
      open(nextUrls, trigger.getAttribute("data-slideshow-title") || "");
      return;
    }
    if (event.target.closest("[data-slideshow-close]")) {
      event.preventDefault();
      close();
      return;
    }
    if (event.target.closest("[data-slideshow-prev]")) {
      event.preventDefault();
      step(-1);
      return;
    }
    if (event.target.closest("[data-slideshow-next]")) {
      event.preventDefault();
      step(1);
    }
  });

  document.addEventListener("keydown", function (event) {
    if (root.hidden) return;
    if (event.key === "Escape") close();
    if (event.key === "ArrowLeft") step(-1);
    if (event.key === "ArrowRight") step(1);
  });
})();
