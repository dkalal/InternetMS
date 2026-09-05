# Financial Terms Guide

**System:** JBMS / InternetMS
**Audience:** Users, finance staff, managers, technicians, and other stakeholders
**Purpose:** Kueleza kwa lugha rahisi istilahi za fedha zinazotumika kwenye mfumo. Majina ya kwenye mfumo yameachwa kwa English ili yawe rahisi kuyatambua kwenye screen na reports.

## 1. Muhtasari wa msingi

Mfumo unatenganisha mambo haya manne:

1. **Invoice total** - thamani ya invoice yote.
2. **Paid** - kiasi cha fedha kilichopokelewa kupitia receipts.
3. **Credited** - kiasi kilichopunguzwa kwa credit note iliyo-issued.
4. **Outstanding / Due** - kiasi ambacho bado kinadaiwa baada ya paid na credited.

Formula kuu:

```text
Outstanding = Invoice total - Amount paid - Credited total
```

Mfumo haukubali outstanding kuwa chini ya sifuri.

## 2. Billing documents

### Quotation
**Quotation** ni pendekezo la bei kwa customer kabla ya mauzo kuthibitishwa. Inaweza kuwa draft, sent, accepted, rejected, expired, au converted kuwa invoice. Quotation si deni mpaka invoice itolewe.

### Invoice
**Invoice** ni hati rasmi inayoonyesha bidhaa au huduma iliyotolewa na kiasi anachotakiwa kulipa customer. Invoice inaweza kuwa ya bidhaa, huduma, au subscription.

### Receipt
**Receipt** ni uthibitisho wa fedha iliyopokelewa kutoka kwa customer na kuhusishwa na invoice maalum. Receipt ndiyo inayoongeza **Amount paid**.

- Kwa inventory/cart invoice, full payment inahitajika kabla stock haijakatwa.
- Kwa service invoice, installments zinaweza kuruhusiwa.
- Receipt peke yake haimaanishi subscription period imelipwa kikamilifu; invoice inayohusiana lazima iwe settled kikamilifu.

### Credit note
**Credit note** ni hati ya kupunguza thamani ya invoice, kwa mfano kwa overcharge, bidhaa iliyorejeshwa, discount iliyoidhinishwa, au service adjustment.

Credit note:

- Si cash payment na haiongezi **Amount paid**.
- Huongeza **Credited total**.
- Hupunguza **Outstanding**.
- Haiwezi kuzidi balance ambayo bado haijalipwa kwenye invoice.
- Ikiwa ime-void, punguzo lake linaondolewa na outstanding huongezeka tena.

### Void
**Void** ni kughairi hati rasmi bila kufuta historia yake. Hati iliyo-void haihesabiwi kama active financial transaction.

### Superseded / Reissued
**Superseded** au **Reissued** inaonyesha invoice ya awali imebadilishwa na invoice mpya. Invoice ya awali inabaki kwa audit trail; haifutwi kimya kimya.

## 3. Amounts kwenye invoice

### Invoice total / Total
**Invoice total** ni kiasi cha mwisho cha invoice baada ya discounts na tax.

```text
Invoice total = Subtotal - Document discount + Tax amount
```

### Line total
**Line total** ni thamani ya kila item kwenye invoice.

```text
Line total = Quantity x Unit price - Line discount
```

### Subtotal
**Subtotal** ni jumla ya line totals kabla ya document-level discount na VAT/tax.

### Unit price
**Unit price** ni bei ya unit moja ya item au service kwenye line ya invoice.

### Discount
**Discount** ni punguzo la bei. Linaweza kuwa kwenye line moja au invoice nzima.

- **Line discount:** punguzo kwenye item maalum.
- **Document discount:** punguzo kwenye invoice nzima.
- **Discount percentage:** punguzo linalohesabiwa kwa asilimia.
- **Discount amount:** kiasi halisi cha fedha kilichopunguzwa.

### VAT / Tax amount
**Tax amount** ni kodi inayohesabiwa kwenye taxable amount.

```text
Tax amount = Taxable subtotal x (Tax rate / 100)
```

Tax-exempt products hazihesabiwi kwenye taxable subtotal.

- Customer mwenye VRN: default tax rate ni 18%.
- Customer asiye na VRN: default tax rate ni 0%.
- Tax rate inaweza kuonekana kwenye invoice na reports kulingana na transaction.

## 4. Payment status na balances

### Amount paid / Paid
**Amount paid** ni jumla ya receipts halali zilizounganishwa na invoice. Hii ndiyo fedha ambayo mfumo ume-record kuwa imepokelewa.

### Credited total / Credited
**Credited total** ni jumla ya issued credit notes zinazohusiana na invoice. Hii ni reduction ya deni, si malipo ya fedha.

### Outstanding / Due / Remaining balance
**Outstanding** au **Due** ni kiasi ambacho bado kinadaiwa kwenye invoice baada ya malipo na credit notes.

```text
Outstanding = Invoice total - Amount paid - Credited total
```

### Partial payment
**Partial payment** ni malipo ambayo hayajamaliza invoice yote. Outstanding hubaki na invoice haija-settled kikamilifu.

### Full payment / Settlement
**Full payment** au **Settlement** ni pale outstanding inakuwa zero. Kwa subscription, ndipo service coverage ya kipindi husika inathibitishwa.

### Credit capacity
**Credit capacity** ni kiwango cha juu cha credit note kinachoweza kutolewa kwenye invoice kwa wakati huo. Ni sawa na current remaining balance.

### Due date
**Due date** ni tarehe ambayo invoice inatarajiwa kulipwa.

### Overdue
**Overdue** ni invoice au billing period ambayo bado haijalipwa wakati due date imepita.

### Payment reference
**Payment reference** ni namba ya muamala, kama bank slip number, mobile-money transaction ID, au reference nyingine ya malipo.

## 5. Customer account balances

### Balance brought forward / Prior balance at issue
**Balance brought forward** ni deni la awali la customer lililonaswa wakati invoice mpya inatengenezwa.

Hili ni deni lililokuwepo kabla ya invoice mpya; si line mpya ya bidhaa na halitozwi VAT tena kama invoice item.

### Amount due at issue
**Amount due at issue** ni jumla ya deni la awali pamoja na invoice mpya.

```text
Amount due at issue = Prior balance + Current invoice total
```

### Current invoice balance
**Current invoice balance** ni outstanding ya invoice hii pekee.

### Current account outstanding
**Current account outstanding** ni jumla ya balances ambazo customer bado anadaiwa kwenye invoices zake zote zilizo wazi.

Hii inaweza kuwa kubwa kuliko outstanding ya invoice moja.

### Customer unpaid balance
**Customer unpaid balance** ni jumla ya amounts ambazo hazijalipwa kwenye subscription periods katika customer workflow. Ikitofautiana na invoice outstanding, sababu inaweza kuwa workflow inayotazamwa ni tofauti: invoice-level receivable dhidi ya subscription-period status.

## 6. Mfano: Invoice 99

Kwa invoice 99 mfumo unaonyesha:

| Term | Amount |
|---|---:|
| Invoice total | TZS 100,000 |
| Amount paid | TZS 50,000 |
| Credited total | TZS 50,000 |
| Outstanding / Due | TZS 0 |

Maana yake ni:

- Customer amelipa TZS 50,000 kupitia receipt.
- TZS 50,000 nyingine imepunguzwa na issued credit note.
- Deni lililobaki ni sifuri.
- **Credited TZS 50,000 haimaanishi customer alilipa TZS 50,000 nyingine.** Ni adjustment iliyopunguza invoice.

```text
TZS 100,000 - TZS 50,000 paid - TZS 50,000 credited = TZS 0 outstanding
```

## 7. Pricing terms

### Buying price / Unit cost
**Buying price** au **Unit cost** ni gharama ambayo business imelipa kupata product. Kwa kawaida haitakiwi kuonekana kwa users wasio na permission ya cost visibility.

### Selling price / Standard price
**Selling price** au **Standard price** ni bei ya kawaida inayotozwa customer.

### Customer pricing tier
**Customer pricing tier** ni category inayosaidia kuchagua bei, kwa mfano Standard, Technician, au Wholesale.

### Technician price
**Technician price** ni bei maalum kwa transaction ya technician. Ikiwa haijawekwa, mfumo unaweza kutumia selling price.

### Wholesale price
**Wholesale price** ni bei maalum inayotumika pale wholesale imewezeshwa, customer/transaction inaruhusiwa, na minimum quantity imetimia.

### Pricing mode
**Pricing mode** inaonyesha source ya bei iliyotumika kwenye line, kama Standard, Technician, Wholesale, Promotion, au Manual.

### Promotion
**Promotion** ni rule ya bei inayoweza kutoa percentage discount, fixed discount, free months, au wholesale pricing.

### Free months
**Free months** ni miezi ya service iliyotolewa bila malipo kama sehemu ya promotion. Si payment na si discount ya cash; inatenganishwa na paid months.

## 8. Inventory na profitability

### Purchase
**Purchase** ni stock iliyopokelewa kutoka kwa supplier. Purchase iliyo-confirmed ndiyo inayoathiri live inventory.

### Purchase total cost
```text
Purchase total cost = Quantity x Unit cost
```

### Available stock
**Available stock** ni quantity iliyopo na inaweza kuuzwa. Drafts au unpaid invoices hazihifadhi stock kama reservation ya mwisho.

### Average cost
**Average cost** ni weighted average ya gharama ya stock iliyoingia.

```text
Average cost = (Old quantity x Old average cost + Incoming quantity x Incoming cost) / New quantity
```

### Stock value / Inventory value
```text
Stock value = Available quantity x Average cost
```

### Stock movement
**Stock movement** ni record isiyobadilishwa kirahisi inayoonyesha stock iliyoingia, kuuzwa, ku-adjust, au opening balance.

### Stock adjustment
**Stock adjustment** ni correction ya stock kwa sababu kama damage, expiry, loss, opening balance, au manual correction.

### Net revenue
**Net revenue** ni revenue ya inventory sale iliyokamilika na kulipwa kikamilifu, baada ya applicable reductions.

### Operational gross profit
```text
Operational gross profit = Net revenue - Recorded stock cost
```

Hii ni kipimo cha uendeshaji wa inventory, si accounting net profit. Haijumuishi kila expense ya biashara kama mishahara, rent, bank charges, au taxes nyingine.

### Supplier balance
**Recorded supplier balance** ni gharama za purchases zilizothibitishwa ukitoa supplier payments zilizorekodiwa. Ni operational record ya mfumo, si lazima iwe full accounting payable ledger.

## 9. Services na subscriptions

### Package
**Package** ni internet offering yenye speed, monthly fee, na setup fee.

### Monthly fee
**Monthly fee** ni gharama ya recurring ya package kwa mwezi.

### Setup fee
**Setup fee** ni gharama ya awali ya kufunga au kuanzisha service.

### Total first month
```text
Total first month = Monthly fee + Setup fee
```

### Subscription
**Customer subscription** ni agreement ya customer na package, pamoja na tarehe na signup price.

### Billing period / Subscription period
**Billing period** ni muda maalum wa service unaotozwa, unaoonyeshwa kwa period start na period end.

### Original amount
**Original amount** ni kiasi cha billing period kabla ya discount.

### Final amount
**Final amount** ni kiasi baada ya discount.

```text
Final amount = Original amount - Discount amount
```

### Paid service coverage
**Paid service coverage** ni kipindi cha service kilichothibitishwa kwa sababu subscription periods husika zimelipwa kikamilifu.

### Paid-through date
**Paid-through date** ni tarehe ya mwisho ambayo service coverage imelipwa. Paid subscription periods ndizo source ya ukweli; tarehe ya receipt pekee haitoshi.

### Active service
**Active service** ni hali ya operational ya internet connection iliyofungwa. Inaweza kuwa tofauti na financial status ya subscription, kwa mfano connection inaweza kuwa active lakini invoice haijalipwa.

## 10. Tax identity

### TIN
**TIN** ni Taxpayer Identification Number ya customer au business.

### VRN
**VRN** ni VAT Registration Number. Ipo ikiwa customer amesajiliwa kwa VAT.

### Tax-exempt product
Product yenye `tax_eligible=False` haijumuishwi kwenye VAT calculation.

## 11. Kanuni za kutafsiri reports

- **Paid**: pesa iliyopokelewa na ku-recordiwa.
- **Credited**: punguzo la invoice kwa credit note.
- **Outstanding**: deni lililobaki kwenye invoice.
- **Current account outstanding**: deni lililobaki kwenye invoices zote za customer.
- **Gross profit**: kipimo cha revenue dhidi ya stock cost, si profit ya mwisho ya kampuni.
- **Invoice status `Paid`**: invoice-level balance imefika zero; haimaanishi kila account ya customer haina deni.
- **Service status `Active`**: connection ipo operational; haimaanishi invoice zote zimelipwa.

## 12. Audit na controls

Financial documents hazipaswi kubadilishwa kwa kufuta history. Corrections zinapaswa kufanywa kwa workflow husika, kama credit note, void, reissue, receipt reversal/void process inapopatikana, na audit log.

Kwa kila reconciliation, finance team inapaswa kulinganisha:

1. Invoice total.
2. Receipts na payment references.
3. Issued credit notes na status zao.
4. Outstanding balance.
5. Customer account outstanding.
6. Service coverage au stock completion, pale inapohusika.

---

**Important scope note:** Mwongozo huu unaeleza maana na calculations za JIMS. Hauchukui nafasi ya accounting policy, tax advice, au statutory financial statements za kampuni.
