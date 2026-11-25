"""
Adapter family for Iranian payment gateways (PSPs).

What these gateways have in common (the shape this family models; every real PSP still has to be read against its own
current documentation and verified in its own sandbox):

* amounts are in Rial (Toman only as a display unit; the adapter converts if the gateway wants Toman);
* the customer is redirected to the gateway, then returned to OUR callback URL by the browser, carrying a reference and
  a status flag that anybody can forge -> an untrusted event that must be confirmed with a server-to-server verify call;
* merchant credentials (merchant id / terminal / api key) are issued after a local merchant contract (Iranian
  business registration, e-namad etc. — a business prerequisite, not a code matter);
* automated refunds are frequently unavailable -> `supports_refund=False` by default; refunds are then manual.
"""

from .base import BasePaymentProvider, ProviderCapabilities


class IranianGatewayProvider(BasePaymentProvider):
    capabilities = ProviderCapabilities(
        kind="iranian",
        supported_currencies=("IRR",),
        supports_refund=False,
        supports_partial_refund=False,
        supports_server_verification=True,
    )
    always_verify = True
