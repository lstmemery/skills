# When to Mock

Mock at **system boundaries** only:

- External APIs (payment, email, etc.)
- Databases (sometimes - prefer test DB)
- Time/randomness
- File system (sometimes)

Don't mock:

- Your own classes/modules
- Internal collaborators
- Anything you control

## Choosing a test double

Pick collaborators in fidelity order; take the first that gives the test the feedback it needs:

1. **Real implementation** — default for everything you control.
2. **Fake** — a working lightweight stand-in (in-memory store, stub server) when the real one is slow, nondeterministic, or unavailable.
3. **Mock** — only when neither a real nor a fake collaborator can trigger the path: hard boundaries (external APIs, time, randomness), rare paths like timeouts and error responses.

A double hides the boundary it replaces. Where a mock or fake stands in for a hard boundary, keep at least one integration or contract test against the real thing, so the suite still notices when the real contract drifts.

## Verifying interactions

Verify an interaction only when the interaction **is** the contract — an outbound side effect the caller cannot observe through a return value or state ("email was sent", "charge was attempted"). Incidental call-count verification (`expect(repo.find).toHaveBeenCalledTimes(2)`) is brittle, couples the test to implementation, and can give false confidence; assert returned values or state changes instead.

```typescript
// Hard to verify through state: the side effect is the contract, so a mock is legitimate
function sendReceipt(order, mailer) {
  return mailer.deliver({ to: order.email, template: "receipt" });
}
```

## Designing for Mockability

At system boundaries, design interfaces that are easy to mock:

**1. Use dependency injection**

Pass external dependencies in rather than creating them internally:

```typescript
// Easy to mock
function processPayment(order, paymentClient) {
  return paymentClient.charge(order.total);
}

// Hard to mock
function processPayment(order) {
  const client = new StripeClient(process.env.STRIPE_KEY);
  return client.charge(order.total);
}
```

**2. Prefer SDK-style interfaces over generic fetchers**

Create specific functions for each external operation instead of one generic function with conditional logic:

```typescript
// GOOD: Each function is independently mockable
const api = {
  getUser: (id) => fetch(`/users/${id}`),
  getOrders: (userId) => fetch(`/users/${userId}/orders`),
  createOrder: (data) => fetch('/orders', { method: 'POST', body: data }),
};

// BAD: Mocking requires conditional logic inside the mock
const api = {
  fetch: (endpoint, options) => fetch(endpoint, options),
};
```

The SDK approach means:
- Each mock returns one specific shape
- No conditional logic in test setup
- Easier to see which endpoints a test exercises
- Type safety per endpoint
