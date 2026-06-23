// Site-wide constants (contact, copy) kept in one place.

export const CONTACT_EMAIL = "anuj.saini@topfolio.in";
export const CONTACT_TOOLTIP = "Drop an email here if you're interested in more credits.";

/** mailto link prefilled for credit requests. */
export const CONTACT_MAILTO = `mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(
  "Review Lens — more credits",
)}`;
