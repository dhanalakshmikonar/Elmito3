(() => {
  const cfg = JSON.parse(document.getElementById('razorpay-config').textContent);
  const status = document.getElementById('pay-status');
  const button = document.getElementById('pay-button');
  const csrf = (document.cookie.match(/csrftoken=([^;]+)/) || [])[1];

  const post = (url, fields) => {
    const form = document.createElement('form');
    form.method = 'post';
    form.action = url;
    Object.entries(fields).forEach(([k, v]) => {
      const input = document.createElement('input');
      input.type = 'hidden'; input.name = k; input.value = v;
      form.appendChild(input);
    });
    document.body.appendChild(form);
    form.submit();
  };

  const reportFailure = (reason) => {
    if (!csrf) return;
    fetch(cfg.failedUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrf, 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ razorpay_order_id: cfg.order_id, reason }),
    }).catch(() => {});
  };

  const open = () => {
    if (typeof Razorpay === 'undefined') {
      status.textContent = 'We couldn’t load the payment window. Check your connection and try again.';
      return;
    }
    const checkout = new Razorpay({
      key: cfg.key, amount: cfg.amount, currency: cfg.currency, name: cfg.name,
      description: cfg.description, order_id: cfg.order_id, prefill: cfg.prefill, theme: cfg.theme,
      // Success is never trusted here: the server verifies the signature before confirming anything.
      handler: (r) => {
        button.disabled = true;
        status.textContent = 'Confirming your payment…';
        post(cfg.callbackUrl, {
          razorpay_order_id: r.razorpay_order_id,
          razorpay_payment_id: r.razorpay_payment_id,
          razorpay_signature: r.razorpay_signature,
        });
      },
      modal: { ondismiss: () => { status.textContent = 'Payment not completed. You can try again.'; } },
    });
    checkout.on('payment.failed', (r) => {
      status.textContent = 'That payment didn’t go through. You can try again.';
      reportFailure((r.error && r.error.description) || 'Payment failed');
    });
    status.textContent = 'Opening secure payment…';
    checkout.open();
  };

  button.addEventListener('click', open);
  window.addEventListener('load', () => setTimeout(open, 300));
})();
