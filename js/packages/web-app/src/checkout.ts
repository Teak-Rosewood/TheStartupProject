import type { Checkout } from "./api";

declare global {
  interface Window {
    Razorpay?: new (opts: Record<string, unknown>) => { open(): void };
  }
}

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    if (window.Razorpay) return resolve();
    const el = document.createElement("script");
    el.src = src;
    el.onload = () => resolve();
    el.onerror = () => reject(new Error("could not load the payment form"));
    document.head.appendChild(el);
  });
}

export interface RazorpayResult {
  razorpay_payment_id: string;
  razorpay_signature: string;
  razorpay_order_id?: string;
  razorpay_subscription_id?: string;
}

/** Opens Razorpay Checkout. Resolves with the signed callback, or null when the customer closes the form.
 *  The callback only updates the screen; access is granted when the verified webhook arrives. */
export async function openRazorpay(co: Checkout, scriptUrl: string): Promise<RazorpayResult | null> {
  if (co.provider !== "razorpay") throw new Error(`${co.provider} checkout is not supported in this app yet`);
  await loadScript(scriptUrl);
  return new Promise((resolve) => {
    const rzp = new window.Razorpay!({
      ...co.checkout,
      handler: (r: RazorpayResult) => resolve(r),
      modal: { ondismiss: () => resolve(null) },
    });
    rzp.open();
  });
}
