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

  function overlayOn() {
    root.removeAttribute("hidden");
    root.classList.add("is-open");
    root.style.position = "fixed";
    root.style.top = "0";
    root.style.right = "0";
    root.style.bottom = "0";
    root.style.left = "0";
    root.style.zIndex = "9999";
    root.style.display = "flex";
    root.style.alignItems = "center";
    root.style.justifyContent = "center";
    root.style.padding = "1.5rem";
    document.body.classList.add("slideshow-open");
    document.body.style.overflow = "hidden";
  }

  function overlayOff() {
    root.classList.remove("is-open");
    root.setAttribute("hidden", "");
    root.style.display = "none";
    document.body.classList.remove("slideshow-open");
    document.body.style.overflow = "";
  }

  function render() {
    if (!urls.length) return;
    image.src = urls[index];
    image.alt = title.textContent ? title.textContent + " photo " + (index + 1) : "Listing photo";
    caption.textContent = index + 1 + " / " + urls.length;
    const many = urls.length > 1;
    prev.disabled = !many;
    next.disabled = !many;
    prev.style.visibility = many ? "visible" : "hidden";
    next.style.visibility = many ? "visible" : "hidden";
  }

  function open(nextUrls, heading) {
    urls = nextUrls;
    index = 0;
    lastFocus = document.activeElement;
    title.textContent = heading || "";
    overlayOn();
    render();
    root.querySelector(".slideshow-close").focus();
  }

  function close() {
    if (!root.classList.contains("is-open")) return;
    overlayOff();
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

  overlayOff();

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
    if (!root.classList.contains("is-open")) return;
    if (event.key === "Escape") close();
    if (event.key === "ArrowLeft") step(-1);
    if (event.key === "ArrowRight") step(1);
  });
})();
