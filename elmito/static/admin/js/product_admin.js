(function () {
  "use strict";

  function setupProductPhotoPreview() {
    const input = document.querySelector("[data-product-image-input]");
    if (!input) return;

    const row = input.closest(".form-row") || input.parentElement;
    const preview = document.createElement("div");
    preview.className = "product-image-preview";
    preview.setAttribute("aria-live", "polite");
    row.appendChild(preview);

    let objectUrls = [];
    input.addEventListener("change", function () {
      objectUrls.forEach(function (url) { URL.revokeObjectURL(url); });
      objectUrls = [];
      preview.replaceChildren();

      const files = Array.from(input.files || []);
      if (!files.length) return;

      const heading = document.createElement("p");
      heading.className = "product-image-preview__heading";
      heading.textContent = files.length + (files.length === 1 ? " photo selected" : " photos selected");
      preview.appendChild(heading);

      const grid = document.createElement("div");
      grid.className = "product-image-preview__grid";
      files.forEach(function (file) {
        if (!file.type.startsWith("image/")) return;
        const url = URL.createObjectURL(file);
        objectUrls.push(url);
        const item = document.createElement("figure");
        item.className = "product-image-preview__item";
        const image = document.createElement("img");
        image.src = url;
        image.alt = "Preview: " + file.name;
        const caption = document.createElement("figcaption");
        caption.textContent = file.name;
        item.append(image, caption);
        grid.appendChild(item);
      });
      preview.appendChild(grid);
    });

    window.addEventListener("pagehide", function () {
      objectUrls.forEach(function (url) { URL.revokeObjectURL(url); });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setupProductPhotoPreview);
  } else {
    setupProductPhotoPreview();
  }
})();
