# Entity-Relationship Diagrams

Mermaid `erDiagram`. Attributes shown are the **key** ones; full column lists are in `DATABASE_DESIGN.md`.
Legend: `PK` primary key, `FK` foreign key, `UK` unique. ➕ entities are additions to your list.
All tables also have `created_at`/`updated_at` (omitted). Internal PK is `bigint identity`; every externally visible entity also has `public_id` (UUID, unique).
Delete behaviour is not drawn; see `DATABASE_DESIGN.md` §3 (SQL Server forbids multiple cascade paths, so cascades are Django-level and commerce history uses PROTECT).

## 1. Accounts
```mermaid
erDiagram
  User ||--o| CustomerProfile : has
  User ||--o| UserPreference : has
  User ||--o{ Address : owns
  User ||--o{ CustomerSession : "identified as"
  User ||--o{ OTPChallenge : "verifies via"
  User }o--o{ Role : "staff roles"
  CustomerProfile }o--o| LoyaltyTier : "member of"

  User {
    bigint id PK
    uuid public_id UK
    string phone_e164 UK "nullable, filtered unique"
    string email UK "nullable, filtered unique"
    string password "usable or unusable"
    string user_type "customer or staff"
    bool is_active
    datetime phone_verified_at
  }
  CustomerProfile {
    bigint user_id PK
    string first_name
    string last_name
    date birth_date
    bool marketing_consent
  }
  Address {
    bigint id PK
    bigint user_id FK
    string label
    string recipient_name
    string recipient_phone
    string country_code
    string province
    string city
    string line1
    string postal_code
    bool is_default
  }
  UserPreference {
    bigint user_id PK
    string language "fa en ar tr"
    string display_currency "IRT default"
  }
  CustomerSession {
    bigint id PK
    string token_hash UK
    bigint user_id FK "nullable"
    string language
    datetime last_seen_at
  }
```

## 2. Catalogue
```mermaid
erDiagram
  Brand ||--o{ Product : makes
  Brand ||--o{ BrandAlias : "known as"
  Brand ||--o{ BrandTranslation : translated
  Category ||--o{ Category : "parent of"
  Category ||--o{ Product : classifies
  Category ||--o{ CategoryTranslation : translated
  Category ||--o{ SizeChart : "has"
  Product ||--o{ ProductVariant : "sold as"
  Product ||--o{ ProductImage : shows
  Product ||--o{ ProductTranslation : translated
  Product ||--o{ ProductAttributeValue : "described by"
  Product ||--|| ProductListingProjection : "read model"
  ProductVariant ||--o{ ProductImage : "variant images"
  ProductVariant ||--o{ ProductVariantOption : "defined by"
  ProductAttribute ||--o{ AttributeOption : offers
  ProductAttribute ||--o{ ProductAttributeValue : typed
  AttributeOption ||--o{ ProductVariantOption : chosen
  AttributeOption ||--o{ AttributeOptionTranslation : translated

  Brand {
    bigint id PK
    uuid public_id UK
    string slug UK
    string name
    bool is_active
  }
  Category {
    bigint id PK
    bigint parent_id FK
    string slug UK
    string path "materialized path"
    int depth
    int sort_order
  }
  Product {
    bigint id PK
    uuid public_id UK
    string slug UK
    bigint brand_id FK
    bigint category_id FK
    string status "draft in_review active archived"
    string source_language
    datetime new_until
  }
  ProductVariant {
    bigint id PK
    uuid public_id UK
    bigint product_id FK
    string sku UK
    string option_signature "UK with product"
    string fulfillment_policy "DIRECT_ONLY PROXY_ONLY DIRECT_THEN_PROXY"
    bigint preferred_external_variant_id FK "nullable"
    bool is_active
  }
  ProductImage {
    bigint id PK
    bigint product_id FK
    bigint variant_id FK "nullable"
    string storage_key
    int sort_order
    string checksum
  }
  ProductTranslation {
    bigint id PK
    bigint product_id FK
    string language
    string name
    string description
    string search_text "normalised"
    string status "machine needs_review approved human"
    string source_text_hash
  }
  ProductAttribute {
    bigint id PK
    string code UK
    string value_type "text number enum bool"
    bool is_variant_axis
  }
  AttributeOption {
    bigint id PK
    bigint attribute_id FK
    string code
    string swatch "colour value"
  }
  ProductListingProjection {
    bigint product_id PK
    decimal min_final_irr
    decimal max_final_irr
    int max_discount_percent
    bool is_available
    string fulfillment_summary
    decimal popularity_score
  }
```

## 3. External sources and imports
```mermaid
erDiagram
  ProductSource ||--o{ ExternalProduct : lists
  ProductSource ||--o{ ImportJob : "imported by"
  ProductSource ||--o{ ExternalBrandMapping : maps
  ProductSource ||--o{ ExternalCategoryMapping : maps
  ProductSource ||--o{ ExternalSizeMapping : maps
  ExternalProduct ||--o{ ExternalProductVariant : "has variants"
  ExternalProduct }o--o| Product : "linked to canonical"
  ExternalProductVariant }o--o| ProductVariant : "linked to canonical"
  ExternalProductVariant ||--o{ SourcePrice : "price history"
  ExternalProductVariant ||--o{ SourceAvailability : "availability history"
  ExternalProduct ||--o{ ProductMatchCandidate : "match proposals"
  Product ||--o{ ProductMatchCandidate : "candidate for"
  Product ||--o{ ProductMergeLog : "merge audit"
  ImportJob ||--o{ ImportJobItem : contains
  ImportJob ||--o{ ImportLog : logs
  ImportJobItem }o--o| ExternalProduct : touched
  ImportJobItem ||--o{ ImportLog : logs

  ProductSource {
    bigint id PK
    string code UK "trendyol namshi ..."
    string country_code
    string base_currency
    string integration_type "API FEED MANUAL MOCK"
    bool is_active
    string credential_ref "name only, never a secret"
  }
  ExternalProduct {
    bigint id PK
    bigint source_id FK
    string external_id "UK with source"
    string external_url
    string raw_title
    string raw_brand
    string raw_category_path
    string raw_attributes "json"
    string payload_hash
    bigint canonical_product_id FK "nullable"
    string match_status
    decimal match_confidence
    datetime last_seen_at
  }
  ExternalProductVariant {
    bigint id PK
    bigint external_product_id FK
    string external_variant_id "UK with product"
    string sku
    string gtin
    string size_raw
    string color_raw
    bigint canonical_variant_id FK "nullable"
    bigint current_price_id FK "pointer"
    bigint current_availability_id FK "pointer"
  }
  SourcePrice {
    bigint id PK
    bigint external_variant_id FK
    decimal list_amount
    decimal sale_amount
    string currency_code
    datetime observed_at
  }
  SourceAvailability {
    bigint id PK
    bigint external_variant_id FK
    string status "in_stock low out unknown"
    int quantity_hint
    datetime observed_at
  }
  ImportJob {
    bigint id PK
    uuid public_id UK
    bigint source_id FK
    string job_type
    string status
    string idempotency_key UK
    bigint requested_by_id FK
    bool cancel_requested
    string celery_task_id
  }
  ImportJobItem {
    bigint id PK
    bigint job_id FK
    string external_id "UK with job"
    string action "created updated unchanged skipped failed"
    string error_code
  }
  ImportLog {
    bigint id PK
    bigint job_id FK
    bigint item_id FK "nullable"
    string level
    string message
  }
  ProductMatchCandidate {
    bigint id PK
    bigint external_product_id FK
    bigint product_id FK
    decimal score
    string signals "json"
    string status "pending approved rejected"
    bigint reviewed_by_id FK
  }
```

## 4. Currency and pricing
```mermaid
erDiagram
  Currency ||--o{ CurrencyRate : "base of"
  Currency ||--o{ CurrencyRate : "quote of"
  PricingRuleSet ||--o{ PricingRule : contains
  ProductVariant ||--|| VariantPrice : "current price"
  ProductVariant ||--o{ PriceHistory : "price changes"
  VariantPrice }o--|| CurrencyRate : "computed with"
  VariantPrice }o--|| PricingRuleSet : "computed under"
  PriceQuote ||--o{ PriceQuoteItem : contains
  PriceQuote }o--|| PricingRuleSet : "rule version"
  PriceQuoteItem ||--o{ PriceQuoteComponent : itemised
  PriceQuoteItem }o--|| CurrencyRate : "rate used"
  PriceQuoteItem }o--|| ProductVariant : prices

  Currency {
    string code PK "TRY AED USD EUR IRR IRT"
    int decimal_places
    string base_currency "IRT -> IRR"
    int base_factor "10"
    bool is_source
    bool is_display
  }
  CurrencyRate {
    bigint id PK
    string base_code FK
    string quote_code FK
    decimal rate "18,10"
    string provider
    bool is_manual
    datetime rate_at
    bigint created_by_id FK
  }
  PricingRuleSet {
    bigint id PK
    int version UK
    string status "draft active archived"
    datetime effective_from
  }
  PricingRule {
    bigint id PK
    bigint rule_set_id FK
    string scope "global source brand category product"
    bigint scope_id
    int priority
    string component_kind
    decimal percent
    decimal fixed_irr
    string basis
  }
  VariantPrice {
    bigint variant_id PK
    decimal list_irr
    decimal final_irr
    int discount_percent
    bigint rate_id FK
    bigint rule_set_id FK
    datetime computed_at
  }
  PriceHistory {
    bigint id PK
    bigint variant_id FK
    decimal old_final_irr
    decimal new_final_irr
    string cause
    bigint rule_set_id FK
    bigint rate_id FK
  }
  PriceQuote {
    bigint id PK
    uuid public_id UK
    string status "open locked expired"
    string display_currency
    datetime expires_at
    bigint rule_set_id FK
    decimal total_irr
  }
  PriceQuoteItem {
    bigint id PK
    bigint quote_id FK
    bigint variant_id FK
    int quantity
    decimal original_amount
    string original_currency
    decimal fx_rate
    datetime fx_rate_at
    decimal converted_irr
    decimal fees_irr
    decimal markup_irr
    decimal discount_irr
    decimal final_unit_irr
    decimal final_line_irr
  }
  PriceQuoteComponent {
    bigint id PK
    bigint item_id FK
    string kind
    decimal amount_irr
    bigint rule_id FK
  }
```

## 5. Inventory
```mermaid
erDiagram
  Warehouse ||--o{ InventoryItem : stores
  ProductVariant ||--o{ InventoryItem : "stocked as"
  InventoryItem ||--o{ InventoryMovement : "changed by"
  InventoryItem ||--o{ InventoryReservation : "held by"
  Warehouse {
    bigint id PK
    string code UK
    string country_code
    string city
    bool is_active
  }
  InventoryItem {
    bigint id PK
    bigint variant_id FK
    bigint warehouse_id FK
    int on_hand
    int reserved
    int safety_stock
  }
  InventoryMovement {
    bigint id PK
    bigint inventory_item_id FK
    string movement_type
    int quantity_delta
    string reference_type
    string reference_id
    bigint actor_id FK
  }
  InventoryReservation {
    bigint id PK
    bigint inventory_item_id FK
    string holder_type "cart order"
    string holder_id
    int quantity
    datetime expires_at
    string status
  }
```

## 6. Cart, wishlist, orders
```mermaid
erDiagram
  User ||--o{ Cart : owns
  CustomerSession ||--o{ Cart : "guest owns"
  Cart ||--o{ CartItem : contains
  ProductVariant ||--o{ CartItem : "added as"
  User ||--o| Wishlist : owns
  CustomerSession ||--o| Wishlist : "guest owns"
  Wishlist ||--o{ WishlistItem : contains
  Product ||--o{ WishlistItem : saved
  User ||--o{ Order : places
  PriceQuote ||--|| Order : "frozen for"
  Order ||--o{ OrderItem : contains
  Order ||--o{ OrderStatusHistory : "status log"
  OrderItem ||--o{ OrderStatusHistory : "item status log"
  OrderItem ||--o| ProcurementRecord : "proxy purchase"
  OrderItem }o--|| ProductVariant : "snapshot of"
  OrderItem }o--|| PriceQuoteItem : "priced by"
  OrderItem }o--o| ExternalProductVariant : "bought from"

  Cart {
    bigint id PK
    uuid public_id UK
    bigint user_id FK "nullable"
    bigint session_id FK "nullable"
    string status "active merged converted abandoned"
    datetime expires_at
  }
  CartItem {
    bigint id PK
    uuid public_id UK "line id"
    bigint cart_id FK
    bigint variant_id FK "UK with cart"
    int quantity
  }
  Order {
    bigint id PK
    uuid public_id UK
    string code UK "MRL-000001"
    bigint user_id FK "nullable if guest allowed"
    bigint price_quote_id FK
    string status
    string payment_status
    string customer_name
    string customer_phone
    string ship_city
    string ship_line1
    string ship_postal_code
    string shipping_method_code
    decimal subtotal_irr
    decimal shipping_irr
    decimal discount_irr
    decimal total_irr
    string idempotency_key
    datetime placed_at
  }
  OrderItem {
    bigint id PK
    bigint order_id FK
    bigint variant_id FK
    bigint price_quote_item_id FK
    string product_name
    string brand_name
    string sku
    string size_label
    string color_label
    string fulfillment_type
    bigint external_variant_id FK "nullable"
    int quantity
    decimal unit_price_irr
    decimal line_total_irr
    string status
  }
  OrderStatusHistory {
    bigint id PK
    bigint order_id FK
    bigint order_item_id FK "nullable"
    string from_status
    string to_status
    bigint actor_id FK
    string reason
  }
  ProcurementRecord {
    bigint id PK
    bigint order_item_id FK
    string status
    string source_order_ref
    decimal actual_cost_amount
    string actual_cost_currency
    datetime purchased_at
  }
```

## 7. Payments and shipping
```mermaid
erDiagram
  Order ||--o{ Payment : "paid by"
  Payment ||--o{ PaymentAttempt : "tried as"
  Payment ||--o{ PaymentTransaction : ledger
  PaymentAttempt ||--o{ PaymentTransaction : produced
  Payment ||--o{ Refund : "refunded by"
  OrderItem ||--o{ Refund : "refund for"
  Payment ||--o{ PaymentWebhookEvent : "notified by"
  ShippingZone ||--o{ ShippingMethod : offers
  ShippingZone ||--o{ ShippingZoneRegion : covers
  ShippingMethod ||--o{ Shipment : "used by"
  Order ||--o{ Shipment : ships
  Shipment ||--o{ ShipmentItem : carries
  OrderItem ||--o{ ShipmentItem : "shipped in"
  Shipment ||--o{ ShipmentTrackingEvent : tracked
  ShippingZone ||--o{ DeliveryEstimateRule : estimates

  Payment {
    bigint id PK
    uuid public_id UK
    bigint order_id FK
    string provider
    decimal amount_irr
    string currency_code
    string status
    string idempotency_key UK
    string provider_ref "authority token"
  }
  PaymentAttempt {
    bigint id PK
    bigint payment_id FK
    int attempt_no "UK with payment"
    string status
    string redirect_url
    string error_code
  }
  PaymentTransaction {
    bigint id PK
    bigint payment_id FK
    bigint attempt_id FK
    string txn_type "authorize capture verify webhook refund"
    decimal amount
    string currency_code
    string provider_txn_id "UK with provider"
    bool signature_verified
    string raw_payload "redacted json"
  }
  Refund {
    bigint id PK
    bigint payment_id FK
    bigint order_item_id FK "nullable"
    decimal amount_irr
    string status
    string reason
    string idempotency_key UK
    bigint requested_by_id FK
    bigint approved_by_id FK
  }
  PaymentWebhookEvent {
    bigint id PK
    string provider
    string event_id "UK with provider"
    bool signature_valid
    datetime processed_at
  }
  ShippingZone {
    bigint id PK
    string name
    string zone_type "domestic international"
  }
  ShippingMethod {
    bigint id PK
    bigint zone_id FK
    string code
    string carrier
    decimal cost_irr
    decimal free_over_irr
    int min_days
    int max_days
    bool is_active
  }
  Shipment {
    bigint id PK
    bigint order_id FK
    bigint method_id FK
    string leg_type "domestic inbound_international"
    string carrier
    string tracking_number
    string status
  }
  ShipmentTrackingEvent {
    bigint id PK
    bigint shipment_id FK
    string provider_event_id "UK with shipment"
    string status
    string location
    datetime occurred_at
  }
```

## 8. Reviews, AI, notifications, administration
```mermaid
erDiagram
  Product ||--o{ Review : reviewed
  User ||--o{ Review : writes
  OrderItem ||--o| Review : "verifies purchase"
  Review ||--o{ ReviewMedia : attaches
  Review ||--o{ ReviewModeration : moderated
  User ||--o{ AIConversation : starts
  CustomerSession ||--o{ AIConversation : "guest starts"
  AIConversation ||--o{ AIMessage : contains
  AIMessage ||--o{ AIRecommendation : suggests
  Product ||--o{ AIRecommendation : recommended
  AIConversation ||--o{ AIUsageRecord : metered
  Role ||--o{ RolePermissionGrant : grants
  User ||--o{ AdminAuditLog : acts
  NotificationTemplate ||--o{ OutboundMessage : renders
  User ||--o{ OutboundMessage : receives
  SystemSetting }o--|| User : "updated by"

  Review {
    bigint id PK
    bigint product_id FK
    bigint user_id FK
    bigint order_item_id FK "nullable"
    int rating
    string body
    string status
  }
  AIConversation {
    bigint id PK
    uuid public_id UK
    bigint user_id FK "nullable"
    bigint session_id FK "nullable"
    string language
    string status
  }
  AIMessage {
    bigint id PK
    bigint conversation_id FK
    int seq "UK with conversation"
    string role
    string content
    int prompt_tokens
    int completion_tokens
    string safety_flags
  }
  AIRecommendation {
    bigint id PK
    bigint message_id FK
    bigint product_id FK
    int rank
    string reason
  }
  AIUsageRecord {
    bigint id PK
    bigint conversation_id FK
    string provider
    string model
    int prompt_tokens
    int completion_tokens
    decimal cost_amount
    string cost_currency
  }
  Role {
    bigint id PK
    string code UK
    bigint django_group_id FK
    bool is_system
  }
  RolePermissionGrant {
    bigint id PK
    bigint role_id FK
    string permission_codename
  }
  AdminAuditLog {
    bigint id PK
    bigint actor_id FK
    string action
    string object_type
    string object_id
    string changes "json diff"
    string request_id
    string ip_hash
  }
  SystemSetting {
    bigint id PK
    string key UK
    string value "json"
    int version
  }
  NotificationTemplate {
    bigint id PK
    string code UK
    string channel
    string language
  }
  OutboundMessage {
    bigint id PK
    bigint user_id FK "nullable"
    string channel
    string status
    string provider_ref
  }
```

## 9. Cross-domain overview (keys only)
```mermaid
flowchart LR
  subgraph Catalogue
    Brand --> Product
    Category --> Product
    Product --> Variant[ProductVariant]
  end
  subgraph Sources
    Source[ProductSource] --> Ext[ExternalProduct] --> ExtV[ExternalProductVariant]
    Ext -.canonical link.-> Product
    ExtV -.canonical link.-> Variant
    ExtV --> SrcPrice[SourcePrice]
  end
  subgraph Money
    Cur[Currency] --> Rate[CurrencyRate]
    Rules[PricingRuleSet] --> VP[VariantPrice]
    Rate --> VP
    SrcPrice --> VP
    VP --> Variant
    Quote[PriceQuote] --> QItem[PriceQuoteItem]
  end
  subgraph Commerce
    Cart --> CartItem --> Variant
    Order --> OItem[OrderItem]
    QItem --> OItem
    Quote --> Order
    Order --> Payment
    Order --> Shipment
    Variant --> Inv[InventoryItem]
  end
  User --> Cart
  User --> Order
```
