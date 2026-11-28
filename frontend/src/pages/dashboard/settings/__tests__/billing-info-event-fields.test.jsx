import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const mocks = vi.hoisted(() => {
  vi.stubEnv("VITE_MIXPANEL_TOKEN", "synthetic-project-token");
  return {
    mixpanel: {
      init: vi.fn(),
      track: vi.fn(),
      register: vi.fn(),
      get_config: vi.fn(),
      set_config: vi.fn(),
      stop_session_recording: vi.fn(),
    },
    get: vi.fn(),
    post: vi.fn(),
    enqueueSnackbar: vi.fn(),
  };
});

vi.mock("mixpanel-browser", () => ({ default: mocks.mixpanel }));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post },
  endpoints: {
    stripe: {
      getBillingDetails: "/billing/details/",
      updateBillingDetails: "/billing/update/",
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: mocks.enqueueSnackbar }));
vi.mock("src/components/iconify/iconify", () => ({ default: () => null }));

import BillingInfoModal from "../BillingInfoModal";
import { Events } from "src/utils/Mixpanel";

const values = {
  name: "SYNTHETIC_CONTACT_4a1b",
  email: "person@example.test",
  company: "SYNTHETIC_COMPANY_5b2c",
  billingAddress1: "SYNTHETIC_ADDRESS_ONE_6c3d",
  billingAddress2: "SYNTHETIC_ADDRESS_TWO_7d4e",
  postalCode: "90210",
  taxId: "SYNTHETIC_TAX_ID_8e5f",
  country: "US",
  state: "CA",
  city: "San Francisco",
};

const trackedProperties = (eventName) =>
  mocks.mixpanel.track.mock.calls
    .filter(([name]) => name === eventName)
    .map(([, properties]) => properties);

describe("billing information analytics events", () => {
  beforeEach(() => {
    mocks.mixpanel.track.mockClear();
    mocks.get.mockReset();
    mocks.post.mockReset();
    mocks.enqueueSnackbar.mockClear();
    mocks.get.mockResolvedValue({
      data: {
        billing_info: {
          name: "",
          email: "",
          company: "",
          billingAddress1: "",
          billingAddress2: "",
          postalCode: "",
          taxId: "",
          country: "",
          state: "",
          city: "",
        },
      },
    });
    mocks.post.mockResolvedValue({ data: {} });
  });

  it("sends field-presence flags and selection values when billing details are updated", async () => {
    const onClose = vi.fn();
    render(<BillingInfoModal open onClose={onClose} />);
    await waitFor(() => expect(mocks.get).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "Country" })).toHaveValue(""),
    );

    const fields = [
      ["Company (optional)", values.company, null],
      ["Billing Contact Name", values.name, Events.billingNameEntered],
      ["Billing Email ID", values.email, Events.billingEmailEntered],
      [
        "Billing Address 1",
        values.billingAddress1,
        Events.billingAddressEntered,
      ],
      [
        "Billing Address 2 (optional)",
        values.billingAddress2,
        Events.billingAddressEntered,
      ],
      ["Postal Code", values.postalCode, Events.postalCodeEntered],
      ["Tax ID", values.taxId, Events.taxIdEntered],
    ];
    for (const [label, value, eventName] of fields) {
      fireEvent.change(screen.getByRole("textbox", { name: label }), {
        target: { value },
      });
      if (eventName) {
        await waitFor(
          () => expect(trackedProperties(eventName).length).toBeGreaterThan(0),
          {
            timeout: 2000,
          },
        );
      }
    }

    for (const [label, option] of [
      ["Country", "United States"],
      ["State", "California"],
      ["City", "San Francisco"],
    ]) {
      const select = screen.getByRole("textbox", { name: label });
      fireEvent.click(select);
      fireEvent.change(select, { target: { value: option } });
      fireEvent.click(await screen.findByRole("menuitem", { name: option }));
    }

    const submit = screen.getByRole("button", {
      name: "Update Billing Details",
    });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    expect(mocks.post).toHaveBeenCalledWith("/billing/update/", {
      name: values.name,
      email: values.email,
      company: values.company,
      billing_address1: values.billingAddress1,
      billing_address2: values.billingAddress2,
      city: values.city,
      state: values.state,
      country: values.country,
      postal_code: values.postalCode,
      tax_id: values.taxId,
    });
    expect(trackedProperties(Events.updateBillingClicked)).toEqual([
      {
        form_fields: {
          country: "US",
          state: "CA",
          has_company: true,
          has_billing_address2: true,
          has_tax_id: true,
        },
        Country: "US",
      },
    ]);
    expect(onClose).toHaveBeenCalledTimes(1);
    // Field events are debounced: let the last ones fire before checking
    // what every event sent.
    await new Promise((resolve) => setTimeout(resolve, 700));
    for (const eventName of [
      Events.billingNameEntered,
      Events.billingEmailEntered,
      Events.billingAddressEntered,
      Events.postalCodeEntered,
      Events.taxIdEntered,
    ]) {
      expect(trackedProperties(eventName).length).toBeGreaterThan(0);
      trackedProperties(eventName).forEach((properties) =>
        expect(properties).toEqual({}),
      );
    }
    const tracked = JSON.stringify(mocks.mixpanel.track.mock.calls);
    for (const marker of [
      values.name,
      values.email,
      values.company,
      values.billingAddress1,
      values.billingAddress2,
      values.postalCode,
      values.city,
      values.taxId,
    ]) {
      expect(tracked).not.toContain(marker);
    }
    const names = mocks.mixpanel.track.mock.calls.map(([name]) => name);
    expect(names.filter((name) => typeof name !== "string")).toEqual([]);
  });

  it("sends the selected country and state codes when they are chosen", async () => {
    render(<BillingInfoModal open onClose={vi.fn()} />);
    await waitFor(() => expect(mocks.get).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "Country" })).toHaveValue(""),
    );

    for (const [label, option, eventName] of [
      ["Country", "United States", Events.countrySelectionClicked],
      ["State", "California", Events.stateSelectionClicked],
    ]) {
      const select = screen.getByRole("textbox", { name: label });
      fireEvent.click(select);
      fireEvent.change(select, { target: { value: option } });
      fireEvent.click(await screen.findByRole("menuitem", { name: option }));
      await waitFor(
        () => expect(trackedProperties(eventName).length).toBeGreaterThan(0),
        { timeout: 2000 },
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 700));

    for (const [eventName, properties] of [
      [Events.countrySelectionClicked, { country: "US" }],
      [Events.stateSelectionClicked, { state: "CA" }],
    ]) {
      trackedProperties(eventName).forEach((sent) =>
        expect(sent).toEqual(properties),
      );
    }
    const names = mocks.mixpanel.track.mock.calls.map(([name]) => name);
    expect(names.filter((name) => typeof name !== "string")).toEqual([]);
  });
});
