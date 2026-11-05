# Architecture

Sections 4 to 6 of the design specification (23 October 2026, revised 27–29 October 2026): the mathematics, the structure and the modules. The tags in square brackets say what each statement rests on; the design rules (D-numbers) are in [decisions](decisions/).

## The source tags

Every statement of fact, every equation and every number carries a tag saying what it rests on. A statement without a tag is a plain description of something tagged nearby.

| Tag | Meaning | Where to check it |
| --- | --- | --- |
| [I3] | A requirement of the component | Section 1 |
| [D016] | A design rule, by number | Appendix A |
| [Der] | A derivation; the working is shown where the tag appears | In place |
| [R6] | A published source | Appendix C |
| [HA] | Home Assistant core source, checked in release 2026.10.3 | Appendix C |
| [Sim] | Shown by the simulations, within the limits stated there | Section 11 |
| [Set] | A setting chosen by judgement, with no derivation | Appendix B2 |
| [Prop] | A design choice this specification makes, with its reason | Appendix B1 |
| [NYE] | NOT YET ESTABLISHED. No basis yet; the text says how it will be established | Appendix B3 |

## 4. The mathematics

### 4.1 The comfort index

How the room feels is measured on a single scale, the comfort index (HCI), in degrees [D009][D010].

Vapour pressure in hPa, from air temperature T (°C) and relative humidity RH (%):

```latex
e = \frac{RH}{100}\times 6.105\,\exp\!\left(\frac{17.27\,T}{237.7+T}\right)
```

This is the form the Bureau of Meteorology publishes with Steadman's apparent temperature [R2][D010].

The index:

```latex
HCI = T + 0.33\,e - 4.00
```

- **T + 0.33e − 4.00** is Steadman's shade apparent temperature with the wind term at zero [D010][R2][R3].
- **Nothing is added to it.** T and RH are the room's own measured readings. The sun, people, appliances and air movement reach the index only through the temperature and humidity they cause, which the model learns (4.2, 4.3). No term set by judgement is added to the index, and nothing the component does changes it [D010][D055].

The Bureau's maps of *indoor* apparent temperature use a different fitted formula: 0.89 T + 3.82 (e/10) − 2.56 [R4]. The component uses the shade formula with wind at zero, by decision [D010]. That choice stands; this paragraph records that the two are not the same quantity.

Outdoors, the same formula with wind speed w in m/s, so that indoors and outdoors are one quantity under two conditions [D010][R2]:

```latex
AT_{out} = T_{out} + 0.33\,e_{out} - 0.70\,w - 4.00
```

**The goal in comfort index.** The user sets a band, low and high, in comfort index [D009]. The goal is its centre [I4]. The calculation of Section 4.4 measures every forecast against that centre in comfort index, so humidity counts as the occupant feels it, and the sun counts through the warmth the model forecasts it will bring [D009][Der].

**From an index to a temperature.** For display, the target temperature is the dry bulb at which the index equals the centre, at the room's humidity now [D009].

**Humid air counts as warmer.** At a fixed temperature ∂HCI/∂e = 0.33 > 0, so a more humid room has a higher index: sweat evaporates less readily in humid air [Der][R2].

The index rises with T at fixed RH [Der]:

```latex
\frac{\partial HCI}{\partial T}\Big|_{RH} = 1 + 0.33\,e\,\frac{17.27\times 237.7}{(237.7+T)^2} \;>\; 1
```

So the target is found by halving the interval from 5 °C to 40 °C until the index is within 0.001 of the centre [Set]. The slope is at most 2.29 on that interval, at 40 °C and 100% RH. So 17 halvings guarantee the tolerance; the limit of 60 is never reached [Der]. An answer at either end is held there and reported [Set].

**Dew point** (Magnus form, coefficients 17.62 and 243.12) [Set][R5]:

```latex
\gamma = \ln\frac{RH}{100} + \frac{17.62\,T}{243.12+T}, \qquad T_d = \frac{243.12\,\gamma}{17.62-\gamma}
```

These coefficients come from a different saturation formula than the vapour pressure above. Over 10–40 °C and 20–100% RH the two give dew points no more than 0.055 °C apart [Der: computed over that grid].

**Free cooling** is advised only when the plan is to cool the room, outdoors feels at least 2.0 lower than the room's index (using wind × 0.3, the share taken to reach a room through a window), and the outdoor dew point is at least 1.0 °C below the room's [Set]. What happens when it is advised is [D073].

**Condensation risk** is reported when the setpoint is within 2.5 °C of the room's dew point. It is reported only, never acted on [Set].

### 4.2 The room model

Each room is modelled as two stores of heat: the room air, which the sensor reads, and the building's envelope and mass, which nothing reads. This is the TiTe grey-box model of Bacher and Madsen, which they identified on a real building from its own measurements [R1]. In their model the mass exchanges heat with the room air **and with outdoors** [R1]. This specification adds three paths, each a physical route heat takes [Prop]:

- sun onto the mass, which Bacher and Madsen's selected model also has [R1];
- a direct path from the air to outdoors, for glass and air leakage;
- a daily pattern in the heat from people and appliances.

[image: The room air and the building mass, and the paths heat takes]

```latex
\frac{dT}{dt} = a\,(T_{out}-T) + b\,(M-T) + s\,S + g(t) - U
```

```latex
\frac{dM}{dt} = c\,(T-M) + k\,(T_{out}-M) + m\,S
```

```latex
g(t) = g_0 + \sum_{j=1}^{2}\Bigl[\alpha_j\cos\frac{2\pi j\,h}{24} + \beta_j\sin\frac{2\pi j\,h}{24}\Bigr], \qquad h = \text{hour of day}
```

| Term | What it is | Where it comes from |
| --- | --- | --- |
| T | Room air temperature, °C | The room's sensor [D046] |
| M | Envelope and mass temperature, °C | Never measured. Estimated by the Kalman filter (4.3) [R1][Prop] |
| T\_out | Outdoor temperature, °C | Outdoor sensor, or the forecast's hour now without one [D099]; the forecast when looking ahead [Prop] |
| a | Direct heat path, air to outdoors, per hour | Learned [Prop] |
| b | Heat path, air to mass, as seen by the air, per hour | Learned [R1] |
| c | Heat path, mass to air, as seen by the mass, per hour | Learned [R1] |
| k | Heat path, mass to outdoors, per hour | Learned [R1] |
| s | Sun warming the air at full sun, °C/h | Learned [R1] |
| m | Sun warming the mass at full sun, °C/h | Learned [R1][Prop] |
| S | Share of full sun on the room's glass, 0 to 1 | Below [D023] |
| g(t) | Heat from people, appliances and lights, °C/h, with a daily pattern | Learned [Prop][Sim] |
| U | Rate the unit removes (+) or adds (−), °C/h | The unit, below [Prop] |

**The model is stable and its time constants are real** [Der]. Write the unforced part as dx/dt = A x with

```latex
A = \begin{bmatrix} -(a+b) & b \\ c & -(c+k) \end{bmatrix}, \qquad
\det A = a(c+k) + bk, \qquad \operatorname{tr} A = -(a+b+c+k)
```

With every figure ≥ 0 and a or k above 0, det A > 0 and tr A < 0, so both eigenvalues have negative real part [Der]. The discriminant (a+b−c−k)² + 4bc ≥ 0, so the eigenvalues are real [Der]. The filter carries b and c as logarithms, so both are above zero and the discriminant is too: the two eigenvalues always differ [Der]. Their reciprocals are the room's two time constants: a fast one, the air settling against the mass, and a slow one, the mass settling against outdoors.

**The sun on the glass, S** [D023]:

```latex
S = [\text{sun on the glass}] \times f_{clear} \times \tau_{blind}
```

- **Sun on the glass** comes from the sun's position, the window's compass direction and any overhang [D023]. A configured sun-on-window sensor replaces the geometry [Set].
- **f\_clear** is the weather forecast's clear-sky fraction for that hour, worked out from the forecast's cloud cover c in %: f\_clear = 0.2 + 0.8 × (1 − c/90) below 90%, and 0.2 at or above it. Without cloud cover it is the UV index ÷ 8, at most 1 [Set]. With neither, f\_clear = 1, and the filter's correction for that step is skipped [Prop].
- **τ\_blind** is the blinds' transmission: 1 with no blind, or a blind whose position is unknown; 0.15 at or below 5% open; and 0.15 + 0.85 × the open fraction otherwise. With several blinds it is their average [Set].

**The unit.** What the unit removes depends on how hard it is working, its effort x [Prop]:

- x is the unit's own reading against its setpoint: reading − setpoint when cooling, setpoint − reading when heating [Prop]. The unit holds its own reading at its setpoint, so this is what its own thermostat acts on [D095].
- A compressor-frequency sensor, where there is one, is not the effort. It is the best source for whether the compressor is running (4.8). The setpoint is what the component sends, so the effort has to be measured in what the setpoint moves; a frequency curve would need a second learned mapping from frequency to setpoint, which nothing yet establishes [Prop].

The steady rate at each effort is a curve r(x), learned as part of the model. It is held at fixed efforts x₁ < x₂ < … < x₆, joined by straight lines, and built from positive steps so that it always rises with effort [Prop][Der]:

```latex
r(x_i) = \sum_{j \le i} e^{\rho_j}
```

The unit reaches that rate after a delay θ and a first-order lag τ [Prop]:

```latex
\tau\,\frac{dU}{dt} = r\bigl(x(t-\theta)\bigr) - U
```

θ and τ are learned as two states of the filter (4.3) [Prop].

Also learned, outside the filter:

- U\_min, the lowest rate the unit holds without stopping by itself [Prop].
- The electrical draw at each effort, per outdoor unit [D028][Prop].

**Humidity** is modelled in vapour pressure e, which changes only when water moves; relative humidity also changes with temperature [Der]:

```latex
\frac{de}{dt} = h\,(e_{out} - e) + q - D_{mode} + R_{coil}(t)
```

- **h** is exchange with outdoor air and **q** a steady indoor source [Prop].
- **D\_mode** is the water removed: the drying rate in dry mode, the response in cool mode, zero otherwise [D026][D029][Prop].
- **R\_coil** is water the coil gives back for 15 minutes after the compressor stops cooling or drying, learned in its own right [D060].

**Why moisture is modelled apart from heat** [D026][Der]. In a humid climate the two loads move apart. On a rainy day the air temperature falls while humidity climbs, so the heat load drops as the moisture load rises, and a room can need drying on a day that feels cool. A model with one figure for both is wrong on exactly those days.

**What the model leaves out** is listed in Section 12, with a confidence for each.

### 4.3 How the model learns

One Kalman filter per room learns everything in the model at once, as it runs. Its state is the room air, the building mass **and every figure of the model**, each with its uncertainty. Every reading corrects all of them together. Nothing else is stored: no records and no window of days [Prop][I3].

[image: One Kalman filter learns the room as it runs; nothing else is stored]

Treating unknown figures as part of a Kalman filter's state is the extended Kalman filter used as a parameter estimator [R6][R16]. The model is the one Bacher and Madsen identified by maximum likelihood with a Kalman filter [R1]; here the same filter carries the figures as well as the temperatures [Prop]. Ljung showed that a filter used this way can give biased figures, or diverge, when its noise model is wrong [R16]. Two things answer this. The filter learns its sensor's noise (below). And nothing depends on a figure being the room's true physical value: in simulation the settled figures differed from the true ones by up to 34% with the sensor's rounding alone, and by up to 85% with a noisy sensor or a disturbed room, while the room stayed inside its band in every run (Section 11.4) [Sim]. The figures are whatever forecasts the room, and only the forecasts are tested (M1) [Prop].

**The state** [Prop]:

```latex
z = \bigl(T,\; M,\; \ln a,\ln b,\ln c,\ln k,\ln s,\ln m,\; g_0,\alpha_1,\beta_1,\alpha_2,\beta_2,\; \rho^{cool}_{1..6},\; \rho^{heat}_{1..6},\; \ln\tau,\; \theta\bigr)
```

- 27 numbers. The positive figures are carried as logarithms, so none can go below zero; g₀ and the wave weights may take either sign [Der].
- **The unit's delay θ and lag τ** are the last two states, τ as its logarithm and never below 1 minute, θ held between 0 and 30 minutes. The delayed rate is taken in proportion between the two recorded minutes either side, so θ is learned as a smooth number. They start at θ = 6 and τ = 15 minutes, with uncertainty 25 min² for θ and 1 for ln τ, and noise per step of 10⁻³ min² and 10⁻⁵ [Prop][NYE]. A separate fit after each start, stop or setpoint change was tried and could not recover them at the sensor's 0.2 °C steps: it found 11 and 1 minutes against a true 6 and 15. The filter found 8–10 and 9–12, their total 19–20 against a true 21 [Sim].
- The unit's curve is carried as its six steps in each direction, so it always rises with effort (4.2) [Der].

**Every 5 minutes: predict** [R6][Prop]:

- The model of 4.2 is run forward 5 minutes from z, in 1-minute steps, with the step's average outdoor temperature and sun, and the unit's rate r(x) passed through its delay and lag. That gives the predicted state z⁻.
- F is the change in z⁻ for a small change in each element of z, computed numerically.
- P⁻ = F P Fᵀ + Q.

**Every 5 minutes: correct** [R6]:

```latex
\nu = y - T^-, \quad S_\nu = P^-_{11} + R, \quad K = P^- e_1 / S_\nu, \quad z = z^- + K\nu, \quad P = (I - K e_1^\top)\,P^-
```

- **The reading's noise R is learned from the readings** [Prop][R17][Sim]. The room's sensor is outside the component's control, so the component learns how far to trust it. When the filter is right, ν² averages P⁻₁₁ + R [R17]. So R = the running average of ν² − P⁻₁₁, never below q²/12. q is the sensor's step: the smallest non-zero change seen in the room's readings, and q²/12 is the variance of its rounding [Der]. The running average gives each correction a weight of 1/288, about one day. For the first day, R = q²/12 [Prop]. In simulation it learned 0.104–0.108 °C for a sensor whose true error was 0.115 °C, and 0.058–0.061 °C for a sensor with rounding alone, whose q/√12 is 0.058 °C. In a room five times more disturbed than the filter assumes it learned 0.068–0.089 °C: disturbance the filter does not expect is taken as sensor noise [Sim].
- **No correction** is made from a reading that was stood in or missing [D074], on a step whose compressor state is unknown (4.8) or whose unit ran in a mode that does not say cooling or heating (heat_cool, auto), on a step whose sun had to be assumed (4.2), or before the filter has seen the unit's last 40 minutes since start-up, because until then it does not know the unit's rate through its delay and lag [Prop]. The prediction still runs.
- **While the unit runs**, the air's noise is raised by (0.5 °C/h × 5 min)², because the unit's rate is itself learned [Prop][NYE: the 0.5 °C/h is a judgement; replay test M7 checks it].

**It builds on itself** [Der]. Each correction narrows P, so every reading adds to what earlier readings taught. The noise Q on each figure is 10⁻⁶ per step in the logarithm, which lets a figure follow the seasons: over a year it allows a drift of about 38% (√(10⁻⁶ × 105,120) = 0.32 in the logarithm) [Prop][Der][NYE: replay test M4]. The noise on the air and mass is 10⁻³ and 10⁻⁴ °C² per hour [Prop][NYE].

**Day one.** The filter starts from a = 0.2, b = 0.2, c = 0.3, k = 0.1, s = 0.4, m = 0.1, zero heat pattern and an untried unit curve, each with a wide uncertainty [Prop][Sim]. In simulation the whole loop held the room within its band from the first day (Section 11.4) [Sim]. A room is seeded by running the same filter over the recorder's history, where the recorder holds any [Prop].

**A change in the room or the unit** (new blinds, a moved sensor, a serviced or failing unit) needs no test and no reset [Prop][Sim]. The noise Q on each figure lets the filter follow a change as it happens. In simulation, a room that suddenly lost heat 50% faster and got half the sun, and a unit that suddenly delivered only 60% of its rate, were each followed without any action. The room stayed inside its band throughout; the unit's learned effectiveness settled at 0.65–0.69 against a true 0.60 (Section 11.4) [Sim].

**What the simulations show** [Sim] (Section 11.2): one-hour forecast errors of 0.061–0.062 °C on the second day and 0.027–0.063 °C from day 30 to day 90; six-hour errors of 0.041–0.055 °C over the same days. The mass estimate was off the true mass by 0.30–0.34 °C on average over the last 30 days.

**Why the mass is a state of its own.** Treating the mass as a running average of the room's readings assumes heat reaches the mass only from the room air [Der]. Bacher and Madsen's model, and real buildings, also connect the mass to outdoors [R1]. On three simulated rooms, with figures fitted by maximum likelihood, a filter with a mass state forecast one hour ahead 3.2–3.9 times more accurately than a running-average mass, and six hours ahead 8.3–8.9 times [Sim].

**Trust.** Every hour, each resting hour's one-hour forecast is compared with what happened [Prop]:

```latex
E_{48} = \frac{1}{N}\sum_{j=1}^{N}\bigl|\hat T(t_j \mid t_j - 1\,h) - T(t_j)\bigr|, \qquad \text{trusted} \iff E_{48} \le 0.3\,^{\circ}C \;\wedge\; N \ge 24
```

The same test is applied to six-hour forecasts over 14 days. Trust is published, and decides only whether the learned controller of 4.9 may take over; the plan runs from day one [Prop][NYE: the 0.3 °C, 48, 24 and 14 are judgements, to be set from replay, M3].

**Learning the unit outside the filter** [Prop]:

- **Lowest continuous rate.** U\_min, one for cooling (and dry) and one for heating, each on its own curve, is the rate of a 30-minute run without a stop held at or below the lowest rate offered at the time, with an effort asked in every minute; it replaces the last, up or down. The lowest rate offered is never below the curve's first point, which is used until a run is learned [D100].
- **Draw.** When exactly one outdoor unit starts or stops and nothing else changes within a minute, the step in house load is its draw at that effort [D028]. Between such efforts it is joined by straight lines; beyond them the nearest known value is used [Prop].

**Learning humidity.** A second, smaller filter of the same kind carries e and the figures h, q, the drying rate, the cooling response and the coil rebound, each learned only from steps where it acts [D026][D060][Prop][NYE: not simulated].

**What uses the model.** Every other part asks the model; none keeps its own idea of the room [Prop]:

| Who | What it asks |
| --- | --- |
| The plan (4.4) | The room's index for every candidate plan, six hours ahead |
| The PID (4.5) | The figures a, b, c and k for its gains, and the unit's delay and lag |
| Delivery (4.6) | The setpoint that delivers a rate, from the unit's curve |
| Precool (Section 3) | The plan to the band, which starts when the model says it must |
| The power budget and demand forecast (4.7) | The energy each plan uses, from the learned draw |
| The arbiter and the learned controller (4.9) | Trust, from the model's forecast errors; each day simulated on the model; the simulator the learned controller trains on |

### 4.4 Deciding what to do: one calculation on the model

Every 5 minutes the room's model is used to find the cheapest way, over the next six hours, to keep the room near the centre of its band. Only the first half-hour of that plan is used; 5 minutes later it is worked out again from what the filter knows then [Prop][Sim]. This is model-predictive control [R12], deciding through the room's model [I2]. The same review finds that practical use in buildings is "still in the early stages" [R12]; the simulations in Section 11 and the acceptance tests in 11.6 are this design's evidence [Prop].

[image: The model chooses the path; the PID holds the room on it]

**The calculation** [Prop]:

```latex
J = \sum_{i=1}^{72} w\,\Bigl(\frac{HCI_i - HCI^*_i}{h_i}\Bigr)^2\delta \;+\; \sum_{j=1}^{12} p_j\,E_j \;+\; c_{start}\,N_{starts}
```

| Term | What it is | Where it comes from |
| --- | --- | --- |
| i | Each 5-minute point of the next 6 hours, δ = 1/12 h | [Prop] |
| j | Each half-hour step of the next 6 hours, Δ = 0.5 h | [Prop][Sim] |
| HCI\_i | The room's comfort index forecast at point i by the model, for that plan | 4.1, 4.2 |
| HCI\*\_i | The centre of the band in force at point i | [D009][I4]; Section 3 |
| h\_i | Half the width of that band: distance is measured in half-bands, so the band the user set is the leeway | [I4][Der] |
| p\_j | The price in step j, $/kWh; the same in every step without a tariff | [D004][D035] |
| E\_j | The energy the plan uses in step j, from the learned draw | [D028] |
| N\_starts | Compressor starts in the plan | [Prop] |
| w | What sitting at the band's edge costs, per hour | [I4][Der] |
| c\_start | What one start costs | [Prop][NYE] |

**Comfort comes first** [D008]. Plans are compared first by the time the forecast index spends outside the band, and only among the plans with the least time outside by J [Der: a strict order, so no saving can buy time outside the band]. The forecast at point i counts as outside when it comes within one standard deviation σ\_i of its own uncertainty of either edge [Prop][Sim]. σ\_i² is the filter's uncertainty in the room carried forward through the model to point i, plus the sensor's learned noise R (4.3), and σ\_i is turned into comfort index by the slope ∂HCI/∂T of 4.1 [Der]. While the model is new σ\_i is wide and the plan keeps well inside the band; as the model learns, σ\_i narrows and the band's full width can be used [Der][I4]. A room is short of its band only when nothing allowed can hold it: the unit at its top rate, an enforced power ceiling [D041][D063], or a limit of Section 3.

**The actions in each step** [Prop]:

- **rest:** compressor off, unit on its fan [D057];
- **dry** [D029];
- **cool** at each rate on the learned curve from U\_min to the top [Prop];
- **heat** at each rate on the learned curve from U\_min to the top [Prop];
- **blinds** closed or open, where the room allows blind control, in a step with the sun on the glass; in a step without it they are left alone, and sun that cannot be worked out counts as none [D022][D023].

**What is not allowed in any plan** (Section 3): a start inside the compressor's minimum rest or a stop inside its minimum run, where the running kind can carry on; where it cannot, the plan may rest and Limits' guard holds the stop [D016]; anything a tariff constraint forbids [D034]; a draw above an enforced ceiling [D041][D063]; heating a waking room [D085]; a mode the unit did not advertise [D024]; a compressor start in an occupied room during the 10 minutes after free cooling is advised [D073].

**The search** [Prop]: plans are built one step at a time, keeping the 50 with the least time outside the band and then the least J (a beam search). The best plan's first step is used, together with the room temperature it forecasts every 5 minutes through that step, which the PID follows (4.5) [Prop][NYE: the 50 is a judgement; the simulation used it].

**What comes out of the calculation, with no rule for it** [Der]:

| Behaviour | Why the calculation produces it |
| --- | --- |
| The weather does the work when it can | Resting costs no energy, so a plan that lets outdoors bring the room to the centre is cheaper than one that runs the unit |
| A room the weather is already warming is heated only if the weather would not bring it to the centre soon enough | Heating costs energy, and the weather moves the room toward the centre for nothing; heating pays only for the distance from the centre it saves. In simulation, heating fell from 21–23% of the time with the PID alone to 0–1.0% (11.3, 11.4) [Sim] |
| Blinds before the compressor | Closing a blind removes heat for no energy [D022] |
| Blinds opened to winter sun | When the room needs warming, sun on the glass adds heat for no energy, so the plan with the blind open costs less [Der] |
| Rain is read correctly | Rain lowers the sun on the glass and raises humidity. The forecast carries both: less sun lowers the forecast temperature and more moisture raises the forecast index, so the plan sees the two in one number [Der] |
| Dry or cool | Each is forecast in comfort index, which counts humidity, and the cheaper way to the centre wins [D029] |
| A cheaper hour is used when one is coming | p\_j is lower then [D035] |
| Few starts | Starts cost c\_start, and the minimum run and rest are hard limits. In simulation, 1.1–2.0 starts a day against 49–53 for the PID alone (11.3, 11.4) [Sim] |
| No hunting for the centre | Inside the band, distance costs little near the centre and the most at the edge, so the unit runs to move the room toward the centre only where that is cheap: when energy is cheap, or when the room is near the edge [I4][Der] |

**Setting w and c\_start** [I4][Der][Sim]. The band is the leeway: the plan aims for the centre, and anywhere inside the band is good [I4]. Holding the centre harder than that makes the component hunt for it [I4]. So w is set so that a room sitting at the edge of its band costs, each hour, what running the unit at its lowest rate for that hour costs at the dearest price:

```latex
w = p_{max}\,D(U_{min}), \qquad c_{start} = 0.5\,p_{max}\,D_1 \times 1\,\text{h}
```

where p\_max is the dearest price in the tariff (1 without one), D(U\_min) the learned draw at the unit's lowest rate, and D₁ the learned draw at 1 °C/h [Der]. Nothing here is chosen by the user: the band sets the scale, and the unit and the tariff set the price [Der]. At any cheaper price the centre is worth relatively more, so the plan moves the room toward it when energy is cheap [Der]. In simulation c\_start = 0.5 cut starts from 4.3–6.0 to 1.3 a day at w = 0.5 (11.3) [Sim].

In simulation, with a 3 °C band and U\_min = 1 °C/h, so that D(U\_min) = D₁, this gave w = 0.44 per °C² in the units of 11.3. The room was inside its band in every week of 14 runs of 28 days, at an average 0.29–0.88 °C from the centre, with 1.1–2.0 starts a day (11.4) [Sim]. Holding the centre tightly instead (w = 8 per °C²) kept the room within 0.3 °C of it 96.8–99.5% of the time, with 1.9–4.0 starts a day; the band-derived setting used 10–21% less energy than that [Sim].

**Limits of what was simulated** [Sim]: temperature only, in place of the index; no humidity, blinds or prices that change; the actual weather as the forecast; the true delay and lag. Section 11.5 lists what is still to be checked.

### 4.5 The PID: holding the room on the plan

The plan says what the room should do over the next half-hour and forecasts the room temperature along the way. The PID holds the room on that path, correcting whatever the model has not yet got right [I2][Prop]. Its gains are calculated from the room's learned model: estimate the process, then design the controller as if the estimate were exact, which is a self-tuning regulator [R8].

**The law** [Prop][Sim]. While the plan's first step runs the unit, cooling or heating at rate u\_plan:

```latex
u = u_{plan} + K_p\left[e + \frac{1}{T_i}\int e\,dt + T_d\left(\frac{dT}{dt} - \frac{dT_{path}}{dt}\right)\right], \qquad e = T - T_{path}(t)
```

- **T is the filter's estimate of the room air** from its latest correction (4.3), not the raw reading [Prop][Sim]. The sensor is outside the component's control. Acting on the estimate stops the PID chasing the sensor's noise [Der]. In simulation with 0.1 °C of random sensor error, the room stayed inside its band either way; with the centre held tightly (w = 8), acting on the raw reading held it within 0.3 °C of the centre 86–92% of each week, and acting on the estimate 96.7–100% [Sim].
- **e** is positive when the room is warmer than the plan's path. Positive u is cooling, negative is heating.
- **u stays in the plan's direction**, between U\_min and the unit's top rate [Prop][Sim].
- **dT/dt** is the filter's estimate of the room's rate of change now: the model's drift (forecast.py, DRIFT NOW) less the unit's current rate U, from the latest correction. Like e, it does not act on the raw readings [Der][D086].
- **When the plan rests**, the PID does nothing. Its integral starts again at zero each time the unit starts [Prop][Sim].
- **No wind-up:** the integral grows only while the output is delivered as asked, not held by a limit or a ceiling [D054][R9].
- **When the gains change** with a new plan, the integral is set so that the output does not jump: the law above solved for the integral at the output now [Der].
- **The integral is not kept across a restart** [D031].

**The gains** are worked out again with every plan, from the filter's current figures and the unit's learned delay θ and lag τ [R8][Prop]. From the model, the response of T to U is [Der]:

```latex
G(s) = -\frac{s + c + k}{(s + a + b)(s + c + k) - bc}
```

As s grows, s·G(s) → −1, so over the minutes that matter the room responds as an integrator with initial slope κ′ = 1 [Der]. Its steady-state gain is κ = |G(0)| = (c+k)/(a(c+k)+bk), in hours [Der]. SIMC treats such a process as first-order with time constant τ₁ = κ/κ′ = |G(0)|, with the unit's lag as a second time constant τ₂ = τ [R7][Der]. SIMC's rules for that case, with κ = κ′τ₁ and κ′ = 1, and its recommendation τ\_c = θ for tight control [R7]. Here θ is the unit's learned delay plus half the interval at which the setpoint it holds can actually change: 0.25 minutes for a unit that steps in tenths or finer (the 30-second cycle), 5 minutes for whole-degree alternation (10-minute holds, 4.6). A sampled controller acts on average half an interval late, and K\_c stays finite when the learned delay is 0 [Der]:

```latex
K_c = \frac{1}{\tau_c+\theta}, \qquad \tau_I = \min\bigl(\tau_1,\; 4(\tau_c+\theta)\bigr), \qquad \tau_D = \tau, \qquad \tau_c = \theta
```

SIMC's controller is in series form, C(s) = K\_c (1 + 1/(τ\_I s))(1 + τ\_D s) [R7]. Multiplied out, the parallel form the code uses is [Der]:

```latex
K_p = K_c\left(1 + \frac{\tau_D}{\tau_I}\right), \qquad T_i = \tau_I + \tau_D, \qquad T_d = \frac{\tau_I\,\tau_D}{\tau_I + \tau_D}
```

**Example, illustration only:** θ = 6 min (delay with half the actuation interval), τ = 15 min, τ₁ = 5.2 h give K\_c = 5.0 per hour, τ\_I = 48 min, and K\_p = 6.56 per hour, T\_i = 63 min, T\_d = 11.4 min [Der]. A room 0.5 °C warmer than its path is then asked for 3.3 °C/h more cooling.

**What was simulated** [Sim]: the P and I terms. The D term was not used in the simulations [NYE].

### 4.6 Turning the rate into what the unit does

The unit's own thermostat is the fast inner loop; the component tells it what to hold [D054].

**Rate to setpoint** [Prop]:

- The effort for a rate is read from the learned curve backwards: x = r⁻¹(|u|). A rate above the curve's top asks for the top [D056][Der].
- **Cooling:** setpoint = the unit's own reading − x. **Heating:** setpoint = the unit's own reading + x [Prop][D095].
- A unit that takes only a range is sent the setpoint −1.0 to +1.0 °C [D024].

**Whole-degree setpoints** [Prop]. A unit that holds only whole degrees (or another step) is moved between the two nearest values, spending the share of each 30 minutes that makes the average equal the wanted setpoint s\* [Der]:

```latex
s_{lo} = \text{step}\left\lfloor \frac{s^*}{\text{step}}\right\rfloor, \quad s_{hi} = s_{lo} + \text{step}, \quad f = \frac{s^* - s_{lo}}{\text{step}}, \quad \text{time at } s_{hi} = f \times 30\,\text{min}
```

Each value is held at least 10 minutes [Prop]; where f × 30 or (1 − f) × 30 minutes would be shorter, the nearer value is held for the whole 30 minutes [Prop]. Values stay within the unit's advertised range [D051].

**The other actions:**

- **Dry** is sent with the setpoint cooling at U\_min would have [D078][Prop].
- **Rest** is the compressor off and the unit in fan-only mode at its quietest fan speed. A unit with no fan-only mode is turned off [D057].
- **Blinds** are moved to the plan's position when they are more than 5 points away from it [Set]. A blind whose position is unknown is left out of the plan, with the reason [D022].
- **Fan speed** while working is the first the unit offers of auto, medium, mid, level 3, 3, high; while resting, the first of quiet, silent, low, min, level 1, 1 [Set].
- **Vanes** are moved only while Automatic vane control is on [D049].

### 4.7 Prices, the power budget and the demand forecast

**Prices** come from Abode Power Tariffs, read every 15 minutes; the component holds none of them [D004][Set]. Each half-hour of a plan is priced at the tariff interval it falls in. A cheaper interval coming up is therefore part of every plan, and the room uses it when that is cheaper overall [D035][Der]. A constraint the component does not know is reported, never dropped [D034]. If the tariff cannot be read, every hour is priced the same and a repair is raised; comfort is unchanged [D004][Prop].

**The power budget** [D037][D039][D041][D042][D044][D061][D062][D063][D067]. During a no-grid-import window running to t\_end, the budget works out the steady draw P\* the house's air conditioning may have without importing [I10].

- **Rest of the house, L:** the house load averaged over 15 minutes, measured while neither the outdoor unit in question nor any other enforced outdoor unit was running, because those are taken off the allowance after and must not be counted twice [D067][I10]. Where none of the 15 minutes had them all stopped, each minute's load less the learned draws of those running then [D067].
- **What must be configured:** battery charge, battery size, reserve margin, solar and house load; otherwise nothing is throttled [D044][Set]. The rated discharge is optional [D042].
- **Battery energy available:** E\_avail = SoC/100 × capacity − reserve margin, so the battery is never drawn below the reserve the user configured [D061][Set].
- **With a solar forecast** [D061]: the window, from now to its end, is walked in forecast slices j (a forecast hour partly inside counts for its part inside; time the forecast does not cover counts as no solar), starting from E₀ = max(E\_avail, 0). Each slice adds forecast solar and removes the house and the outdoor unit's steady draw P. The battery can never hold more than it has room for [D061]:

```latex
E_{j+1} = \min\bigl(E_0 + \text{headroom},\; E_j + PV_j - (L + P)\,\Delta_j\bigr), \qquad P^* = \max\{P \le P_{plant} : E_j \ge 0\;\forall j\}
```

- **The plant limit:** P\_plant = max(P\_rated + solar now − L, 0), where a rated discharge is configured; otherwise there is none [D042][D061].
- **The bisection is valid:** more draw never leaves the battery fuller at any slice, so the set of allowable P is an interval from 0, and halving finds its top [Der].
- **Precision:** the halving stops at 0.005 kW [Set].

**Without a solar forecast** [D039][D067] (the P\_rated term only where a rated discharge is configured [D042]):

- S\_sus is the solar now, derated by the worst forecast clear-sky fraction over the rest of the window [D039].
- L\_net = max(L − S\_sus, 0) and the solar credit is max(S\_sus − L, 0) [D039].

```latex
P^* = \text{credit} + \max\!\left(0,\; \min\!\left(\frac{\max(E_{avail},0)}{t_{end}-t_{now}} - L_{net},\;\; P_{rated} - L_{net}\right)\right)
```

**The ceiling** [D037][D041][D062][D063][D067]:

- **Its measure:** the highest rate whose learned draw fits within the outdoor unit's allowance: U\_ceil = max{U : draw(U) ≤ allowance}, the allowance being set below [D037][Prop]. It is found on the curve of the way the room runs: heating while the plan heats, cooling while it cools or dries, and while it rests the way the room must go to reach its band's centre [D028]. In an enforced room no plan may run above it (4.4), nor the PID ask above it (4.5); a changed ceiling is planned under at once. Turned into the other direction's rate for the same power, it holds a plan that turns the other way to the same allowance [D028].
- **How it moves:** it drops at once. It rises only after it has stood 10 minutes, and only as far as the allowance clears the higher draw by 0.3 kW [D062].
- **Guidance** shows the ceiling and never applies it [D041]. Enforced applies it [D041].
- **When even the lowest rate cannot last to t\_end:** the room keeps cooling, with its ceiling at its lowest rate U\_min, so the battery lasts as long as it can [D063][D067][Set].
- **Turning off:** only when the battery cannot carry on: E\_avail ≤ 0 (down to its reserve), or the lowest rate's draw is above P\_plant, or that draw is not yet known. A running enforced room is then turned off, and runs again when the battery can supply it [D063][D067]. A resting room is left alone [D063].
- **Precool is not rationed** [D033][D037][D063].
- **Any missing figure** means no ceiling at all [D044].

**The whole house shares the allowance** [I10]:

1. **Several enforced rooms at once.** P\* is the house's allowance, not each room's. The house decides once per cycle and informs each room [I10]. Each enforced outdoor unit is allowed P\* less the learned draw of the other enforced outdoor units now running, so the total cannot exceed P\* [Der].
2. **Rooms on one outdoor unit.** A multi-head unit is one draw on the battery [I10]. Each room's draw now on the unit is its learned draw at the mean effort of its own running heads, and the unit's draw is the sum over its rooms. A room's allowance is P\* less the other enforced outdoor units now running and less the other rooms now running on its own unit, whatever their power management, because the rest of the house is measured while the unit is not running and so does not hold them [D097][Der]. A room is charged on its scorecard for its own share, not the whole unit's [D097].

**One gap in the budget's mathematics** [Der]:

- **Draw away from start efforts.** Draw is learned only at the efforts where starts and stops happened (4.3). Between them it is interpolated, which is [Prop]. With a frequency sensor the draw can also be learned while running, which is [NYE].

**Grid checks** [D038][D043]:

- **The grid sign** is settled at setup and never changed at run time. House load − solar is what the house draws from the grid and the battery together. At a moment when it is above 0.2 kW and the battery charge has not fallen over 5 minutes [Set], the grid must be importing, so the sign of the grid reading then shows which way positive points; the setup screen is pre-filled from such a moment [Der]. Ten readings in a row that contradict it raise a repair. A reading within 5 W of zero is no evidence and resets the count [D038].
- **Battery power** = house load − solar − grid, published for display. The budget uses the rated discharge, not this reading [D042][Der].
- **Import in a no-import window** is added up over the window. More than 0.05 kWh raises a repair naming the kWh [D043].

**The demand forecast** [D005]:

- **What it gives:** the energy expected from now until the next tariff change, in each tariff interval on the way, what it is priced at, and the constraints in force [D005][Set]. The next tariff change is the start of the first interval ahead whose rate, price or constraints differ from those in force.
- **How it is computed:** each room's plan in force (4.4), its energy added up by interval. Where the next change lies beyond the plans' six hours, the forecast runs to the plans' end and says so [Prop].

### 4.8 Knowing when the compressor really runs

Whether the compressor ran decides which rate the filter is told the unit delivered. It is never read from the mode alone: a unit in cool may be idling at its setpoint [D025].

[image: Whether the compressor is really running comes from the best source there is]

| Source, best first | How it is read |
| --- | --- |
| 0. The head is off or in fan-only | Not running, whatever a frequency sensor says: one sensor is often chosen for every head on an outdoor unit and reads another head's compressor [D025] |
| 1. Compressor frequency | Running above 0 Hz [Prop] |
| 2. `hvac_action` | Cooling, heating or drying is running; idle, fan or off is not [D025] |
| 3. House-load step | Within a minute of a command to that head, with no other unit changing: a rise of at least half the learned draw is a start, the matching fall a stop [Prop] |
| 4. Unknown | Not learned from; the trace says so [Prop] |

The source that answered is recorded every cycle [D006].

### 4.9 The learned controller, and when it may take over

The plan of 4.4 with the PID of 4.5 is in charge from the first minute. A learned controller (RL) runs beside it, is scored, and takes over only once it has proved better on that room. It hands back at once if it fails [I7][Prop].

This is the Simplex pattern: an advanced controller runs beside a simple, well-understood one, and decision logic hands control back to the simple one when needed [R10][R11].

**RL** [I7][Prop]:

- **Its inputs:** the room's state: the distance from the centre, the slope, the model's drift, outdoor temperature, sun, the price now and in the next hour, the time of day, and the mass estimate. Its output is a rate.
- **Training** happens away from the live room, on a simulator built from the room's own learned model and recorded weather. It never explores in the live room: in a field comparison, RL kept a house cooler, "particularly during an initial adaptation phase, leading to three reports of occupant discomfort" [R14].
- **The learning algorithm** is [NYE]. It is to be chosen and shown on the simulator before RL may run even in shadow.

**Scoring** [I7][Prop]. Each day, each controller is scored on minutes outside the band while occupied, and on J of 4.4 for the day.

- **The one in charge** is scored on its real day. The same day is also simulated on the model. If the two differ by more than 0.3 °C on average, no comparison counts that day, because the model cannot judge it [Prop][NYE: 0.3].
- **Why the same day.** A review found that 71% of field demonstrations of MPC and RL used experiment protocols "that may lead to unreliable performance estimates" [R13]. Scoring both on the same day, under the same weather, avoids comparing different days.
- **A win:** RL wins a day when it has no more minutes outside the band and a lower J.
- **Hand up** when the model has had six-hour trust for 14 days and RL won at least 11 of the last 14 days [Prop]. If the two were truly equal and days independent, 11 of 14 would happen by chance with probability 470/16384 = 0.029; 10 of 14 with probability 1471/16384 = 0.090 [Der: binomial, p = 0.5]. Days are not independent, so a run of wins is more likely than the binomial figure says; 11 is recommended over 10 for that reason [Prop].
- **Hand down** at once when the model loses trust, or when the plan and PID's simulated day beats RL two days running [Prop]. If the two were equal, two in a row would happen with probability 0.25. A wrongful hand-down only returns control to the proven controller [Der].
- **On a handover** the PID's integral is set so the output does not jump [Der]:

```latex
\int e\,dt := T_i\left(\frac{u_{now} - u_{plan}}{K_p} - e - T_d\left(\frac{dT}{dt} - \frac{dT_{path}}{dt}\right)\right)
```

## 5. Structure

**Four layers** [D001]. The component sits on top of whatever drives each air conditioner, and sees only a Home Assistant climate entity:

| Layer | What it is | Who provides it |
| --- | --- | --- |
| L3 Coordination | Rooms, modes, the comfort index, the model, the plan, the order of actions, the trace | This component |
| L2 Regulation | The PID that brings the room sensor to target, and compressor protection | This component |
| L1 Device driver | Anything that presents a climate entity | Replaceable, per room |
| L0 Inputs | Sensors, the weather forecast, the battery, the tariff | Read only, never written [D004][D005] |

Because L2 and L3 see only a climate entity, the driver underneath can be replaced room by room without changing this component. A better driver adds compressor frequency, per-head power and fault codes; each improves the model, and none is required [D001].

**The shape of one cycle.** Data flows down. Nothing flows back up except what the model learns and the two notices in the rules below.

```
   Home Assistant (sensors, climate entities, tariff, forecast)
                     |
              +------v------+
              | 1 OBSERVE   |  one snapshot of the room
              +------+------+
          +----------+--------------+
   +------v------+           +------v------+
   | 2 MODEL     |           | 3 COMFORT   |  how the room should feel:
   | learns room |           | band, centre|  the band in force
   +------+------+           +------+------+
          | forecasts, unit         |
          | response, trust         |
   +------v-------------------------v------+
   | 4 ECONOMICS  price, solar, battery,   |  when to spend, how much
   |   power budget, demand forecast       |
   +------------------+--------------------+
   +------------------v--------------------+
   | 5 CONTROL   plan --+                  |  what the room needs,
   |             PID  --+--> ARBITER       |  as a rate
   |             learned+   (one in charge)|
   +------------------+--------------------+
   +------------------v--------------------+
   | 6 DELIVERY  cheapest first:           |  which actuators, what to send
   |   blinds -> fan -> dry -> compressor  |
   +------------------+--------------------+
   +------------------v--------------------+
   | 7 LIMITS    lockout, user controls,   |  what may reach the unit
   |   openings, guard, power              |
   +------------------+--------------------+
              +-------v-------+       +--------------+
              | 8 ACT         |------>| 9 EVIDENCE   |  trace, scorecard,
              | send, record  |       |              |  warnings, repairs
              +---------------+       +--------------+
          10 MEMORY keeps the model, the user's choices and occupancy
```

Ten modules each own one part, and every cycle passes through them in order [Prop]. Each module is one or more .py files in the component [I9]. Decisions live in modules that import nothing from Home Assistant; the coordinator gathers, calls and carries out, and decides nothing [D002].

| # | Module | Owns | Files | Touches Home Assistant |
| --- | --- | --- | --- | --- |
| 1 | Observe | One snapshot of the room, each value tagged by how far it can be trusted | `observe.py` | No (handed raw states) [D002] |
| 2 | Model | The room's model, the filter that learns it, and every forecast | `model/__init__.py`, `model/physics.py`, `model/filter.py`, `model/unit.py`, `model/humidity.py`, `model/trust.py`, `model/forecast.py` | No [D002] |
| 3 | Comfort | Occupancy, the band in force and its centre | `comfort.py` | No |
| 4 | Economics | Prices, the power budget, the demand forecast | `economics.py` | No |
| 5 | Control | The plan, the PID, the learned controller and which is in charge | `control/__init__.py`, `control/plan.py`, `control/pid.py`, `control/rl.py`, `control/arbiter.py` | No |
| 6 | Delivery | The commands that carry out the chosen action | `delivery.py` | No |
| 7 | Limits | What may reach the unit | `limits.py` | No |
| 8 | Act | Sends what is allowed, and records it | `act.py` | Yes |
| 9 | Evidence | The trace, the scorecard, warnings and repairs | `evidence.py` | Builds the trace without it; speech and repairs go through the coordinator |
| 10 | Memory | What survives a restart | `memory.py` | Yes (storage, recorder) |
| — | Integration | Connects the modules to Home Assistant | `__init__.py`, `coordinator.py`, `config_flow.py`, `forms.py`, `const.py`, `models.py`, `entity.py`, `sensor.py`, `switch.py`, `select.py`, `number.py`, `button.py`, `diagnostics.py`, `manifest.json`, `strings.json`, `translations/en.json`, `services.yaml`, `icons.json`, `quality_scale.yaml`, `brand/` | Yes |
| — | Helpers | The comfort index, moisture, the sun, the unit's abilities, matrix arithmetic | `hci.py`, `psychro.py`, `sun.py`, `capabilities.py`, `linalg.py` | No |

**Rules of the structure** [D002][Prop]:

- Modules 1–7 and the helpers import nothing from Home Assistant.
- A module imports only modules earlier in the cycle, plus the helpers, `const.py` and `models.py`.
- Limits tells Control when its output was not delivered, so the PID's integral holds [D054]. Act tells Model what was actually sent, so the filter is told the real command [D081].

**Timing** [Set][Prop]: the cycle runs every 30 seconds [Set]. The filter and the plan run every 5 minutes; the PID every cycle [Prop].

**Where each requirement is met** [Prop]:

| Requirement | Met by | Shown by |
| --- | --- | --- |
| I1 A model learned from each room | 1 Observe, 2 Model | M1–M9, M11; 11.2 |
| I2 The model decides, through one calculation and a PID | 5 Control, 6 Delivery | P2, P4, P5; 11.3 |
| I3 Better as it learns, and keeps what it learned | 2 Model, 10 Memory | M2, M4; 11.4 |
| I4 Comfort is how the room feels; the band is leeway, no hunting | 3 Comfort, 5 Control | P1, L1, L2; starts a day in 11.4 |
| I5 Least cost | 4 Economics, 5 Control | P2, P3 |
| I6 Every decision says why | 9 Evidence, the entities of Section 7 | Unit tests on the trace |
| I7 Plan and PID first; the learned controller earns its place | 5 Control (arbiter) | P5; 4.9 |
| I8 Precool is set by the user, with a button or an action | 3 Comfort, the Precool button and actions of Section 7 | Unit tests on the mode |
| I9 One or more .py files per module | The file layout above | The file list itself |
| I10 One house allowance; one draw per outdoor unit | 4 Economics, 7 Limits | L3 |
| I11 Home Assistant quality scale, platinum | The integration files and helpers | Section 13: its rule table, the tests and coverage of 13.2, and strict typing in CI (13.3) |

## 6. The modules

Each module below says what it does, how it enables the intent, and then gives every file in pseudocode.

### Module 1 · Observe

Observe builds one snapshot of the room each cycle, and says how far each value can be trusted.

**What it does:**

- **Reads nothing itself.** The coordinator hands it raw states, so every decision in a cycle sees the same moment [D002].
- **Ages readings.** A reading's age is from the later of its last change and its last report [D066]. Room temperature and humidity, presence and door and window contacts have no age limit; only `unavailable` or `unknown` makes them missing [D070][D087]. Outdoor readings are too old after 3 hours, and power readings after 15 minutes [D030].
- **Stands in** the unit's own reading for a missing room reading, tagged so the model never learns from it [D074].
- **Stands in** the forecast's value for the hour now for a missing or too-old outdoor temperature or humidity, tagged ESTIMATED. The model learns from it as from the sensor, because the outdoor reading only drives the room and is never what the filter corrects on; a house without an outdoor sensor still learns [D099].
- **Decides whether the compressor ran,** from the best source (4.8).
- **Works out the sun on the glass** (4.2).

**How it enables the intent:**

- **I1.** A model can only learn what is true. Observe separates what was measured from what stood in and what is unknown, so the filter corrects only on measurements.

#### observe.py

```text
FILE observe.py — one snapshot of one room

DESIGN VALUES
  outdoor reading too old after .......... 3 hours                    [D030]
  power reading too old after ............ 15 minutes                 [D030]
  room temperature and humidity, presence,
  door and window contacts: no age limit;
  only "unavailable" or "unknown" is missing ..................... [D070][D087]
  house-load step counted as a compressor start or stop:
      at least half that outdoor unit's learned draw, within 1 minute
      of a command to it, with no other unit changing ............. [Prop]

BUILD SNAPSHOT, for one room, from the raw states the coordinator hands over:

  FOR EACH reading the room uses:
      age = now - the later of (last changed, last reported)          [D066]
      IF it reads "unavailable" or "unknown"      mark it MISSING
      OTHERWISE IF it has an age limit AND age > limit
                                                   mark it MISSING; add it
                                                   to the STALE LIST  [D030]
      OTHERWISE                                    mark it MEASURED

  ROOM TEMPERATURE, and ROOM HUMIDITY, each:
      IF the room sensor is MEASURED                use it
      OTHERWISE IF the unit reports its own value   use that; mark it
                                                    STOOD-IN          [D074]
      OTHERWISE                                     MISSING           [D018]

  THE UNIT, for each head:
      mode; setpoint (the middle, for a unit that holds a range)      [D081]
      hvac_action; its own temperature and humidity; fan speed; vanes
      compressor frequency, where a sensor is configured              [Prop]

  COMPRESSOR STATE, for each head; the first answer that exists wins:
      0. the head off or in fan-only   not running (a frequency sensor
                                       may be another head's)         [D025]
      1. frequency sensor MEASURED     running if above 0 Hz          [Prop]
      2. hvac_action reported          running if cooling, heating or
                                       drying; not if idle, fan or off [D025]
      3. house-load step (above)       started or stopped             [Prop]
      4. otherwise                     UNKNOWN                        [Prop]
      RECORD which source answered                                    [D006]

  SUN ON THE GLASS, 0 to 1:                                           [D023]
      IF a sun-on-window sensor is configured   it says whether the sun
                                                is on the glass       [Set]
      OTHERWISE                                 sun.py SUN ON A WINDOW
      IF not on the glass                       0
      OTHERWISE  f_clear x the blinds' transmission (hci.py)          [Set]
      f_clear, from the forecast for this hour:                 [Set]
          cloud cover c known:  0.2 + 0.8 x (1 - c / 90) below 90 %;
                                0.2 at or above
          otherwise UV known:   UV index / 8, no more than 1
          otherwise             1, and the value is marked ESTIMATED;
                                the filter does not correct on it    [Prop]

  OUTDOOR     temperature, humidity, wind; the hourly forecast, any
              value it leaves out filled from the outdoor sensors now [Prop]
              a temperature or humidity missing or too old: the
              forecast's value for the hour now, ESTIMATED; none for
              the hour now: MISSING                                   [D099]
  OCCUPANCY   presence; Leave pressed; Precool asked for, and its ready
              time if one was given             [D013][D088][I8]
  OPENINGS    each door or window: open or shut, and since when       [D050]
  HOUSE       house load, solar, battery charge, grid power; the
              tariff interval now and ahead                [D004][D037]

  GIVE BACK the snapshot, the STALE LIST, and for every value one tag:
      MEASURED / STOOD-IN / ESTIMATED / MISSING / UNKNOWN
```

### Module 2 · Model

Model is the mathematical model of the room [I1]. It is learned from the room by one Kalman filter, checked against the room, and is the only place any module asks what the room will do [Prop].

**What it does:**

- **Holds the room's physics** (4.2), the unit's curve, delay and lag, and the humidity model.
- **Learns all of it as it runs** with one Kalman filter for heat and one for moisture, corrected every 5 minutes (4.3) [Prop].
- **Judges itself** by its forecasts, and follows a change in the room or the unit as it happens, with no test and no reset (4.3) [Prop].
- **Answers every question** other modules ask about the room. No other module keeps its own idea of the room [Prop].

**How it enables the intent:**

- **I1.** This is the mathematical model of the room [I1].
- **I3.** Every reading adds to what was learned before; nothing is thrown away by a window or a restart [I3][Der].

#### model/physics.py

```text
FILE model/physics.py — the room's heat balance                     [R1][Prop]

FIGURES FROM THE STATE z (filter.py):
  a b c k s m = e^(their logs); g0 al1 be1 al2 be2 as they are
  rate curve, cooling and heating: r(x_i) = sum over j <= i of e^(rho_j)
  at efforts x_1..x_6 (0.5, 1, 1.5, 2, 3, 4 C),
  straight lines between, the top value beyond                    [Prop][Der]

HEAT FROM PEOPLE AND APPLIANCES at hour h:
  g = g0 + al1 cos(2 pi h/24) + be1 sin(2 pi h/24)
         + al2 cos(4 pi h/24) + be2 sin(4 pi h/24)

STEP (z, 5 minutes of inputs) -> z after 5 minutes:                       [Der]
  REPEAT 5 times, one minute each:
      dT = a (outdoor - T) + b (M - T) + s S + g - U
      dM = c (T - M) + k (outdoor - M) + m S
      T = T + dT x 1 min;  M = M + dM x 1 min
  the figures are unchanged by STEP

EXACT 5-MINUTE MATRICES (for plans and forecasts):                         [Der]
  A   = [[-(a+b), b], [c, -(c+k)]]
  Phi = e^(A x 5 min), from A's eigenvalues and eigenvectors (real and
        distinct, 4.2)
  Gam = A^-1 (Phi - I) B,  B = [[a, s, 1, -1], [k, m, 0, 0]]
  next (T, M) = Phi (T, M) + Gam (outdoor, S, g, U)
```

#### model/filter.py

```text
FILE model/filter.py — one Kalman filter learns the room              [R6][R16][Prop]

STATE z, 27 numbers: T, M, ln a, ln b, ln c, ln k, ln s, ln m,
  g0, al1, be1, al2, be2, rho_cool 1..6, rho_heat 1..6, ln lag, delay
UNCERTAINTY P, 27 x 27

DESIGN VALUES                                                         [Prop]
  starting figures ...... a 0.2, b 0.2, c 0.3, k 0.1, s 0.4, m 0.1,
                          heat pattern 0 [Sim]; curve steps ln 1  [NYE]
                          delay 6 min, lag 15 min                  [NYE]
  starting uncertainty .. T 0.01; M 4; each log figure 1; heat
                          pattern 0.25 each [Sim]; curve steps 0.5 [NYE]
                          delay 25 min^2; ln lag 1                 [NYE]
  noise per 5 minutes ... T 1e-3 x 5/60, M 1e-4 x 5/60,
                          each figure 1e-6; delay 1e-3 min^2;
                          ln lag 1e-5                          [Sim][NYE]
  delay held between 0 and 30 minutes; lag at least 1 minute      [Prop]
  extra T noise while the compressor runs  (0.5 C/h x 5 min)^2 [Sim][NYE]
  q = the smallest non-zero change seen in the room's readings
  R starts at q^2 / 12; v (the running average of nu^2) starts at R  [Der]

EVERY 5 MINUTES:
  inputs = the step's average outdoor temperature and sun, the hour,
           and the unit's rate: r(effort) through the delay and lag
           held in z, 0 while resting                        [D081][Prop]
  PREDICT:
      z_new = physics STEP (z, inputs)
      F = for each element of z: (STEP with that element moved by 1e-5
          - z_new) / 1e-5
      P = F P F^T + Q  (+ the extra T noise if the compressor ran)
  CORRECT, unless the room reading is STOOD-IN or MISSING, the
  compressor state is UNKNOWN (or it ran in heat_cool or auto), the
  sun was ESTIMATED, or fewer than 40 minutes of the unit's running
  have been seen since start-up:                           [D074][Prop]
      nu = room reading - z[T]
      IF a day of corrections has been made
          R = the larger of q^2 / 12 and v - P[T,T]        [R17][Prop][Sim]
      S = P[T,T] + R
      v = v + (nu^2 - v) / 288
      K  = P[:, T] / S;  z = z + K nu;  P = P - K P[T, :]
      make P symmetric
  IF any figure is not a finite number
      keep the state from before this step; RECORD why

SEED (Memory, for a new room): the same steps, run over the recorder's
history in time order                                              [Prop]
```

#### model/unit.py

```text
FILE model/unit.py — the unit's response, outside the filter          [Prop]

EFFORT, for one head:
  cooling: unit's reading - setpoint; heating: setpoint - unit's
  reading                                                        [D095]
  a compressor-frequency sensor is not an effort; it answers whether
  the compressor runs (observe.py)                               [Prop]

RATE NOW = r(effort at now - delay), passed through the lag:
  U = U + (r - U) x 1 min / lag, every minute

delay and lag: the two states the heat filter learns (filter.py)  [Prop]

WHEN a head has run 30 minutes or more without the compressor stopping,
     at an average rate at or below the lowest rate offered then, with
     an effort asked in every minute of it:                         [D100]
  U_min for that direction (cooling and dry, or heating) = the run's
  average rate, up or down; a change of direction starts a new run
  lowest rate offered = the larger of U_min and the curve's first point
  (until then, the curve's first point)

WHEN exactly one outdoor unit starts or stops, and nothing else changes
     within a minute (a head joining or leaving an outdoor unit another
     head kept running is not its start or stop, nor is one head stopping
     as another starts on it in the same minute):               [D028]
  draw at that effort = the step in house load (running average)
  between known efforts: straight lines; beyond them: the nearest
WITHOUT a house-load sensor: draw = 1.2 kW at every effort         [D028]

GIVE BACK: rate for an effort, effort for a rate (the top effort if the
rate is beyond the curve [D056]), delay, lag, U_min, draw for a rate
```

#### model/humidity.py

```text
FILE model/humidity.py — how the room's moisture moves      [D026][D060][Prop]

WORKS IN vapour pressure e (hci.py); turned back into relative humidity
with the room's temperature whenever the index needs it            [Der]

A second Kalman filter of the same kind as filter.py:
  STATE: e, ln h, q, ln drying rate, ln cooling response, ln rebound
  MODEL: de/dt = h (e_outdoor - e) + q - drying - cooling x rate
                 + rebound for 15 minutes after the compressor stops
                 cooling or drying                             [D060]
  each figure moves only on steps where it acts: drying in dry mode,
  cooling response while cooling, rebound in the 15 minutes after a
  stop                                                       [D026][D060]
  a step with STOOD-IN, MISSING or UNKNOWN values corrects nothing [D074]
                                                         [NYE: not simulated]
```

#### model/trust.py

```text
FILE model/trust.py — is the model right?                              [Prop]

DESIGN VALUES                                                     [NYE]
  trusted ......... one-hour error <= 0.3 C on average over the last
                    48 resting hours, with at least 24
  six-hour trust .. the same for six-hour forecasts, over 14 days

EVERY HOUR:
  REMEMBER this hour's forecast one hour ahead (unit at rest)
  IF the last hour was resting
      error = |room now - the forecast made an hour ago|; keep 48
  TRUSTED = their average <= 0.3 C AND there are at least 24
  SIX-HOUR CHECK: from the filter's state six hours ago, forecast to now
      with the weather and unit rates that actually happened;
      keep 14 days of errors

EVERY WEEK: store the week's average one-hour error -> the scorecard
```

#### model/forecast.py

```text
FILE model/forecast.py — the questions every other module asks

DRIFT NOW (unit at rest):                                    [Der: 4.2]
  a (outdoor - T) + b (M - T) + s S + g(hour)

FORECAST (a plan, a horizon):
  from T, M and e now (the filters' estimates), step forward 5 minutes at
  a time with Phi and Gam:
      outdoor and sun from the weather forecast for that step, the
      blinds as the plan sets them
      the unit's rate as the plan sets it; humidity by humidity.py
  UNCERTAINTY carried forward with the same steps:                 [Der]
      P2 = the filter's 2 x 2 uncertainty of (T, M) now
      each step: P2 = Phi P2 Phi^T + the T and M noise per 5 minutes
      sigma = square root of (P2[T,T] + R), in C; in comfort index,
              sigma x dHCI/dT at that step's temperature and humidity (hci.py)
  GIVE BACK temperature, humidity, comfort index and sigma at each step

SETPOINT FOR A RATE:                                               [Prop]
  effort = unit.py effort for the rate
  cooling: setpoint = the unit's own reading - effort
  heating: setpoint = the unit's own reading + effort
```

`model/__init__.py` holds one room's parts together and has no logic of its own [Prop].

### Module 3 · Comfort

Comfort turns how the user says the room should feel into the goal the plan aims at: the centre of the band in force, in comfort index [D009][I4].

**What it does:**

- **Decides whether the room is occupied,** with a grace each way. The answer survives a restart [D012][D068].
- **Chooses the band** (the first that applies wins): Leave pressed: off [D088]; Precool: the precool band, from its ready time if one was given [D013][D080][I8]; empty: off [D012]; sleeping: the sleep band [D058][D083]; otherwise the occupied band [D009].
- **Ramps** a change of band over a whole hour [D014][D084], and asks for the room to be decided again at once [D092].
- **Marks a room waking** on its way out of Sleep [D085].
- **Gives the plan its goal** for each half-hour ahead: the band and its centre [I4].

**How it enables the intent:**

- **I4.** The user states a feeling, never a setpoint; the goal is the centre of that feeling's band [D009][I4].

#### comfort.py

```text
FILE comfort.py — occupancy, the band and its centre for one room

DESIGN VALUES
  counts as occupied after presence for ... 2 minutes (room setting)  [Set]
  counts as empty after no presence for ... 10 minutes (room setting) [Set]
  band ramp ............................... 1 hour               [D014]
  precool ends ............................ once occupied, on Leave or
                                            cancel, or 1 hour after its
                                            ready time             [D080]

OCCUPANCY, every cycle:
  IF Leave was pressed and not yet undone                             [D088]
      EMPTY. Undone when presence reads empty and then occupied again,
      or the user changes Automatic control, Comfort mode or asks for
      Precool
  OTHERWISE IF presence has read occupied for the occupied grace  OCCUPIED
  OTHERWISE IF presence has read empty for the empty grace        EMPTY
  OTHERWISE (unknown, or still inside a grace)   keep the last answer
  a room with no presence sensor counts as OCCUPIED                   [Set]
  REMEMBER the answer; Memory keeps it across a restart               [D068]

PRECOOL, only in a room with a presence sensor:                 [I8][Der]
  WHEN the button is pressed           PRECOOL on, ready time now
  WHEN the precool action is called    PRECOOL on, ready time as given,
                                       or now if none                [D013]
  PRECOOL off WHEN the room counts as OCCUPIED, Leave is pressed, the
  cancel_precool action is called, or 1 hour after the ready time    [D080]

SLEEPING:                                                       [D058][D083]
  Comfort mode Sleep      sleeping
  Comfort mode Awake      not sleeping
  Comfort mode Schedule   sleeping while the sleep schedule is on
  a mode the user chose stays until the user changes it            [D083]

THE BAND NOW, the first that applies wins:
  1. Leave pressed                     -> OFF                         [D088]
  2. PRECOOL on                        -> the PRECOOL band, required
                                          only from its ready time  [I8][D013]
  3. the room is EMPTY                 -> OFF                         [D012]
  4. sleeping                          -> the SLEEP band              [D058]
  5. otherwise                         -> the OCCUPIED band
  RECORD which rule chose it                                          [D006]

RAMP and WAKING:
  WHEN the band changes, and neither side is OFF:
      REMEMBER the band changed from, and when                       [D084]
      ask the coordinator to decide this room again at once          [D092]
  FOR the hour after a change: the band moves steadily from the old
      to the new                                                     [D014]
  WHEN the room leaves the SLEEP band     mark it WAKING             [D085]
  WAKING ends once the ramp is over; a room still below its daytime
  band may then be heated                                       [D085][D092]

GIVE BACK:
  OFF (and why), or, for each half-hour of the next 6 hours: the band
  low and high and its centre HCI* in comfort index, whether the band
  is required in that half-hour, WAKING yes or no, and the reason  [I4][Prop]
  the centre as a dry-bulb temperature at the room's humidity now, for
  display (hci.py)                                                  [D009]
```

### Module 4 · Economics

Economics supplies the prices and the power limits the plan works with. It never decides whether the band holds [D008].

**What it does:**

- **Prices every half-hour of a plan** from Abode Power Tariffs, and holds none of it [D004].
- **Runs the power budget** in a no-grid-import window, for rooms set to guidance or enforced [D037][D041].
- **Publishes the demand forecast** for whoever runs the battery, and never writes to the battery [D005].
- **Fails toward comfort.** No price means every hour priced the same; any missing reading means no ceiling [D008][D044].

**How it enables the intent:**

- **I5.** Price and power enter the one calculation as costs and limits; they never decide whether the room is comfortable [D008][D035].
- **I4.** Comfort is traded only in a room the user has set to enforced [D041].

#### economics.py

```text
FILE economics.py — when to spend, and how much

DESIGN VALUES
  tariff read every ....................... 15 minutes                 [Set]
  solar forecast read every ............... 15 minutes                 [D061]
  house load and solar averaged over ...... 15 minutes                 [D067]
  ceiling may rise after .................. 10 minutes, clearing by
                                            0.3 kW                     [D062]
  grid import reported above .............. 0.05 kWh in a window        [D043]
  grid reading within ..................... 5 W of zero is no evidence  [D038]
  grid sign contradicted .................. 10 readings in a row        [D038]
  grid evidence ........................... house load - solar above
                                            0.2 kW, battery charge not
                                            falling over 5 minutes      [Set]
  demand forecast ......................... to the next tariff change  [Set]
  allowance found to within ............... 0.005 kW                    [Set]

TARIFF, every 15 minutes:                                              [D004]
  read the forward intervals from Abode Power Tariffs, in one service
  call, 24 hours ahead so the next tariff change is found: each
  interval's start, end, rate name, price in $/kWh, and constraints
  hold nothing; prices stay in dollars per kWh
  IF a constraint is not one this component knows
      raise a repair naming it; never drop it                         [D034]
  IF the read fails
      raise a repair; price every half-hour the same; comfort
      unchanged                                                 [D004][Prop]

PRICES FOR A PLAN:                                               [D004][D035]
  each half-hour of the next 6 hours gets
  the price of the tariff interval it falls in; with no tariff, or a
  failed read, every half-hour gets the same price                  [Prop]
  p_max = the dearest price among the tariff's intervals (1 with none)

POWER BUDGET, every cycle, once for the whole house, while a no-grid-import
window is in force:                                                [I10]
  IF battery charge, battery size, reserve margin, solar or house load is
     not configured, or is MISSING
      no ceiling for anyone; RECORD which; stop               [D044][Set]
  (rated discharge is optional; without it there is no plant limit) [D042]
  FOR EACH outdoor unit with a room whose power management is not OFF:
                                                             [D041][I10]
      L = house load averaged over 15 minutes, measured while this
          outdoor unit was not running; one running since start-up:
          house load less its estimated draw, every room on it   [D067][D097]
      available = charge / 100 x size - reserve margin                [Set]
      IF a solar forecast can be read:                                 [D061]
          plant = rated discharge + solar now - L, not below 0
                  (no limit without a rated discharge)              [D042]
          P* = the highest steady draw, found by halving between 0 and
               plant, for which the battery, walked slice by slice
               (+ forecast solar, - (L + draw) x hours, never above
               available + room left in the battery), never falls
               below 0                                            [D061][Der]
      OTHERWISE:                                                  [D039][D067]
          sustained = solar now x the worst forecast clear-sky
                      fraction over the rest of the window
          L_net = L - sustained, not below 0
          credit = sustained - L, not below 0
          P* = credit + the larger of 0 and the smaller of
               (available, not below 0) / hours left - L_net
               and rated discharge - L_net (when configured)       [D042]
      each room's draw now on a unit = its learned draw at the mean
          effort of its own running heads; a unit's = the sum over its
          rooms, unknown if any of theirs is                   [D028][D097]
      FOR EACH room on this outdoor unit:
          allowance = P* less the draw now of every other ENFORCED
                      outdoor unit running, less the draw now of every
                      other room on this outdoor unit, whatever its
                      power management                    [I10][Der][D097]
          its ceiling = the highest rate whose learned draw is
                        <= allowance                            [I10][Prop]
      the ceiling drops at once; it rises only after it has stood
      10 minutes, and only to a rate whose draw the allowance clears by
      0.3 kW                                                          [D062]
      IF GUIDANCE    record the ceiling; never apply it                [D041]
      IF ENFORCED    hand the ceiling to Control: no plan may draw
                     more                                       [D041][Prop]
          IF even the lowest rate does not last to the window's end
              ceiling = the lowest rate: cool until the battery cannot
                                                           [D063][D067][Set]
          IF the battery is down to its reserve, or cannot supply the
             lowest rate's draw (plant), or that draw is not known
              ask Limits to turn a running unit off; a resting room is
              left alone                                          [D063][D067]
      precool is never rationed                             [D033][D037][D063]
  RECORD the allowance and each ceiling; "no limit" where none bites  [D096]

GRID CHECKS, only with a grid sensor:                             [D038][D043]
  battery power = house load - solar - grid; published for display  [Der]
  GRID EVIDENCE: a moment when house load - solar is above 0.2 kW and
        the battery charge has not fallen over the last 5 minutes; the
        house must then be importing, so the reading's sign at that
        moment is the import direction                         [Der][Set]
  sign: settled at setup and never changed here; a reading at a moment
        of grid evidence whose sign says export contradicts it; 10 in a
        row -> a repair; a reading within 5 W of zero is no evidence and
        resets the count
  import: add up grid import over each no-import window; at its end,
          above 0.05 kWh -> a repair naming the kWh; cleared by the
          next clean window

DEMAND FORECAST, every 15 minutes:                                 [D005][Prop]
  from now to the next tariff change: the first interval ahead whose
  rate, price or constraints differ from those in force          [Set]
  FOR EACH room, its plan in force (control/plan.py), to that change or
  the plan's end, whichever is first
  kWh in each tariff interval = the sum of each room's planned draw x
  time running in it
  publish the total, the kWh and price per interval, when it runs to,
  whether that is the tariff change, and the constraints in force

GIVE BACK, per room:
  the price of each half-hour ahead, p_max, the ceiling or none, a
  turn-off request (enforced only), and the reason for each
```

### Module 5 · Control

Control decides what the room's unit does. The plan chooses the action from the model; the PID holds the room on the plan's path; a learned controller runs beside them and takes over only on evidence [I2][I7].

**What it does:**

- **The plan** (4.4): every 5 minutes, the cheapest six hours that keep the room near the centre of its band, within everything the user and the equipment allow [I4][Prop].
- **The PID** (4.5): every cycle, holds the room on the plan's path, with gains calculated from the model [I2][R7].
- **RL and the arbiter** (4.9): RL in shadow, scored daily; control passes only on evidence and comes back at once on failure [I7].

**How it enables the intent:**

- **I2.** The action is the minimum of one calculation on the room's model; there is no table of thresholds [I2][Der].
- **I3.** As the filter learns, the plan's forecasts and the PID's gains improve with it [I3][Der].

#### control/plan.py

```text
FILE control/plan.py — the cheapest path to the centre                [R12][Prop]

DESIGN VALUES
  plan every 5 minutes, 6 hours ahead, in 12 half-hour steps     [Prop][Sim]
  keep the 50 best part-plans at each step                    [Prop][Sim][NYE]
  w       = p_max x (learned draw at the lowest rate), dollars per hour
                                                                  [I4][Der]
  c_start = 0.5 x p_max x (learned draw at 1 C/h) x 1 hour     [Prop][Sim][NYE]

EVERY 5 MINUTES, per room, unless Comfort said OFF:
  ACTIONS for one step, only those the unit advertised [D024]:
      REST (fan); DRY; COOL at each curve rate from U_min to the top;
      HEAT at each curve rate from U_min to the top; and, where blind
      control is allowed, in a step with the sun on the glass at any of
      its points, each of these with the blinds closed and with them
      open; in a step without it, the blinds left alone (sun that
      cannot be worked out counts as none)                  [D022][D023]
  NOT ALLOWED in any plan:
      a start within 5 minutes of the last stop, a stop within 10
      minutes of the start (per outdoor unit)               [D016][D020]
      (the stop rule binds only where the running kind can carry on;
      where it cannot, heating a waking room or a mode or rate no
      longer offered, the plan may rest and Limits holds the stop)  [D016]
      anything a tariff constraint forbids                         [D034]
      a draw above an enforced ceiling                        [D041][D063]
      HEAT while the room is WAKING                                [D085]
      a compressor start in an occupied room within 10 minutes of free
      cooling being advised                                        [D073]
  plans = [an empty plan, starting from the filters' T, M and e now]
  FOR EACH of the 12 half-hour steps j:
      FOR EACH plan, FOR EACH allowed action:
          extend the plan; forecast the room to the end of the step,
          5 minutes at a time (model FORECAST)
          FOR EACH of the step's 6 five-minute points i, counted only
          where Comfort says the band is required:           [I4][D013]
              sigma = the forecast's sigma at point i, in comfort
                      index (model FORECAST)                 [Der][Prop]
              outside += 5 minutes IF the index at point i is within
                         sigma of either edge of the band, or beyond
                         it                                  [D008][Prop]
              J += w x ((index - centre) / half the band's width)^2
                   x 1/12 hour                                 [I4][Der]
          J += price in step j x energy in step j (learned draw x time)
                                                             [D035][D028]
          J += c_start for each start
      keep the 50 plans with the least outside, then the least J  [D008]
  best = least outside; among equals, least J                [D008][Der]
  GIVE BACK: best's first-step action and rate, the room temperature it
  forecasts every 5 minutes through that step (the path), and the whole
  plan with its forecast and its cost terms, for the trace          [D006]
```

#### control/pid.py

```text
FILE control/pid.py — holds the room on the plan's path                [R7][Prop]

GAINS, with every plan, from the filter's figures, delay and lag:   [R8]
  tau1 = (c + k) / (a (c + k) + b k)       hours          [Der: |G(0)|, 4.5]
  theta = delay + half the actuation interval: 0.25 min for a unit
          stepping in tenths or finer, 5 min for whole degrees      [Der]
  tc   = theta                                                         [R7]
  Kc   = 1 / (tc + theta)                                              [R7]
  tI   = the smaller of tau1 and 4 x (tc + theta)                      [R7]
  tD   = lag                                                           [R7]
  Kp = Kc (1 + tD / tI);  Ti = tI + tD;  Td = tI tD / (tI + tD)       [Der]

EVERY CYCLE, per room:
  IF the plan's action is REST, DRY or OFF       the PID does nothing
  OTHERWISE (COOL or HEAT at rate u_plan; positive cools):
      e     = the filter's estimate of the room (4.3) - the plan's path
              now                                          [Prop][Sim]
      slope = model DRIFT NOW less U, from the filter's latest estimate,
              not the raw readings' slope [Der]                         [D086]
      u = u_plan + Kp (e + integral / Ti + Td (slope - the path's slope))
      keep u in the plan's direction, between U_min and the top rate
                                                                [Prop][Sim]
      IF the last cycle's output was delivered as asked (Limits did not
         hold it, no ceiling capped it, not at U_min or the top)
          integral = integral + e x time since the last cycle  [D054][R9]
  WHEN the unit starts            integral = 0                  [Prop][Sim]
  WHEN the gains change           set the integral so u does not jump [Der]
  ON A HANDOVER (arbiter.py)      set the integral so u does not jump [Der]
  NOT KEPT across a restart                                         [D031]

GIVE BACK: u, e, the P, I and D terms, the gains
```

#### control/rl.py

```text
FILE control/rl.py — a learned policy (shadow until promoted)          [I7]

DOES NOTHING until a trained policy file exists for this room.

TRAINING, outside Home Assistant:                                      [R14]
  a simulator built from this room's learned model and recorded weather
  the learning algorithm .................................... [NYE]
  never explores in the live room

EVERY CYCLE, once a policy file exists:
  inputs: distance from the centre, slope, drift, outdoor temperature,
          sun, price now and next hour, time of day, the mass estimate
  proposed rate = the policy's answer, within everything plan.py's
  NOT ALLOWED list forbids
```

#### control/arbiter.py

```text
FILE control/arbiter.py — which controller is in charge              [I7][Prop]

DESIGN VALUES
  days of six-hour trust before a hand-up .......... 14                [NYE]
  days RL must win ................................. 11 of the last 14 [Prop][Der]
  the model must reproduce a real day within ....... 0.3 C on average  [NYE]
  hand down when the plan and PID's simulated day wins  2 days running [Prop]

EVERY CYCLE:
  run the plan and PID, and RL; the one IN CHARGE gives the action; the
  other's proposal is recorded                                      [D006]

EVERY DAY, at midnight, per room:
  the one in charge: its real day (from Evidence): minutes outside the
  band while occupied, and J; and the same day simulated on the model
  IF the simulated day differs from the real one by more than 0.3 C
      no comparison counts today; RECORD why
  OTHERWISE
      simulate the day with the other's choices
      RL WINS the day when its minutes outside are no more AND its J is
      lower

HAND UP to RL when BOTH:
  the model has had six-hour trust for each of the last 14 days
  RL won at least 11 of the last 14 days

HAND DOWN to the plan and PID, at once, when:
  the model stops being TRUSTED, OR
  their simulated day beats RL's 2 days running

ON ANY HANDOVER: the PID's integral is set to match the rate being
delivered (pid.py); RECORD who, and why

GIVE BACK: the action and rate from the one in charge, who is in
charge, and both proposals and scores
```

`control/__init__.py` holds one room's controllers and the arbiter together, and has no logic of its own [Prop].

### Module 6 · Delivery

Delivery turns the chosen action and rate into commands the unit and blinds can carry out (4.6).

**What it does:**

- **Rate to setpoint** through the learned curve; the unit's own thermostat does the fast work [D054][Prop].
- **Whole degrees:** alternates between the two nearest setpoints so the average is the one wanted [Prop][Der].
- **Rest, dry and blinds** as the plan chose them [D057][D078][D022].
- **Advises free cooling** where the windows would do better [D073].

**How it enables the intent:**

- **I2.** It adds no decision of its own: every command follows from the plan and the PID [Prop].

#### delivery.py

```text
FILE delivery.py — what to ask of the blinds and the unit

DESIGN VALUES
  whole-degree cycle ........................ 30 minutes               [Prop]
  shortest hold of one setpoint ............. 10 minutes               [Prop]
  blind moved when off by more than ......... 5 points                 [Set]

EVERY CYCLE, per room, given the action, the rate u and the plan's blinds:

  IF Comfort said OFF        step = OFF; stop    (Limits sends it)

  BLINDS, those that report a position, where blind control is allowed:
      move to the plan's position if more than 5 points away   [D022][Set]
      a blind whose position is unknown: left alone; RECORD it     [D022]

  REST:   compressor off; unit on fan-only at its quietest fan speed
          a unit with no fan-only mode: OFF; RECORD it              [D057]
  DRY:    mode dry, with the setpoint COOL would have at U_min
                                                              [D078][Prop]
  COOL or HEAT at rate |u|:
          setpoint = model SETPOINT FOR A RATE (|u|)          [Prop][D095]
          a unit that takes only a range: setpoint -1.0 to +1.0 C   [D024]

  WHOLE DEGREES, whenever a setpoint is to be sent:            [Prop][Der]
      step = the unit's setpoint step (capabilities.py)
      low = the step at or below the wanted setpoint; high = low + step
      f = (wanted - low) / step
      IF f x 30 min < 10 min         send low for the whole 30 minutes
      IF (1 - f) x 30 min < 10 min   send high for the whole 30 minutes
      OTHERWISE send high for f x 30 min of each 30, low for the rest
      never outside the unit's advertised range                     [D051]

  FAN SPEED: while working, the first the unit offers of auto, medium,
  mid, level 3, 3, high; while resting, the first it offers of quiet,
  silent, low, min, level 1, 1                                       [Set]
  VANES: IF Automatic vane control is on    the settled vertical position;
         OTHERWISE none                                             [D049]

  FREE COOLING:                                                     [D073]
      IF psychro.py advises it AND the room is occupied, while the step
         is COOL or its start is held for the advice
          advise opening up (trace, and a warning where one can be heard)
      (the coordinator judges it before the plan's first step goes out:
       when the plan is to cool, the advice starts then and the plan is
       made again at once, holding the start for 10 minutes)
  CONDENSATION:
      IF the setpoint is within 2.5 C of the room's dew point
          RECORD the risk (reported only)                            [Set]

GIVE BACK the proposed step: mode, setpoint, fan speed, vane, blind
positions, and the reason for each
```

### Module 7 · Limits

Limits decides what may reach the unit. It applies the same way whichever controller is in charge, and nothing in it is ever traded [D034][Prop].

**What it does:**

- **User switches and lockout first.** Lockout is off every cycle [D021]. Switching Automatic control off sends off once, then nothing [D049][D089]. Leave is off at once [D088].
- **An empty room is off,** with a warning first only where someone can hear it [D012][D071][D082].
- **An open door or window:** nothing new starts; after the room's grace, with warnings, the unit stops [D050]. One opened on free-cooling advice stops it at once [D073].
- **A missing reading** leaves the unit alone and names what is missing [D018].
- **The unit's own limits:** only what it advertised at setup is sent [D024][D048], and the short-cycle guard counts per outdoor unit [D016][D020]. While a unit is left alone, the guard follows its compressor as it is seen to run, both in whether the outdoor unit is wanted and in its state, so a start made by hand counts when control comes back and lets another room on the unit rest; and its heads as they run are the command a held stop keeps them on, so a start made by hand runs its 10 minutes [D016][D020][D089].
- **Tariff constraints** are held absolutely [D034].
- **Tells Control** whenever it changed or held the step, so the integral holds [D054].

**How it enables the intent:**

- **I4.** The user's controls and the equipment's limits sit above every controller. A controller that has just taken over cannot trade them.
- **I2.** The plan already works inside these limits (4.4); Limits makes sure of it, whichever controller is in charge.

#### limits.py

```text
FILE limits.py — what may reach the unit

DESIGN VALUES (the first two are room settings with these defaults)
  opening grace ...................... 5 minutes                       [D050]
  warning grace ...................... 3 minutes before a stop          [D050]
  short-cycle guard .................. 10 minutes on, 5 minutes off,
                                       per outdoor unit           [D016][D020]

EVERY CYCLE, per room, given the proposed step; the first that applies wins:

  1. LOCKOUT                                                           [D021]
      IF the room has a lockout reason, or shares a climate entity with
         another room
          step = OFF every cycle; nothing else

  2. AUTOMATIC CONTROL                                           [D049][D089]
      WHEN it is switched from on to off
          step = OFF once, now, not held by the minimum run
      WHILE it is off
          step = NONE (send nothing); the room's controls belong to the
          user

  3. LEAVE                                                             [D088]
      WHEN pressed           step = OFF at once, not held by the minimum run
      WHILE left             step = OFF

  4. EMPTY ROOM (Comfort said OFF because the room is empty)  [D012][D082]
      IF a warning can be heard: announcements on, speakers set, a
         speech engine to say it [D069], the room run and awake, and
         its unit on
          warning due now; step = OFF one warning grace later
      OTHERWISE
          step = OFF

  5. AN OPEN DOOR OR WINDOW                                      [D050][D073]
      nothing new is started
      IF it was opened while free cooling was advised
          step = OFF at once, no grace, no warning
      OTHERWISE
          first warning due one warning grace before the stop
          final warning due just before it
          step = OFF when the opening grace ends
      IF when it opened cannot be read      hold the unit as it is

  6. MISSING READING                                                   [D018]
      IF there is no comfort reading or no band
          step = NONE (send nothing); RECORD the missing entity

  7. ENFORCED POWER                                              [D063][D067]
      IF Economics asked for the unit off       step = OFF until the battery
                                                can supply it again

  8. WHAT THE UNIT CAN DO                                        [D024][D048]
      remove any mode, fan speed or vane position not in the room's stored
      profile; a unit that takes only a range gets setpoint -1.0 to +1.0 C

  9. SHORT-CYCLE GUARD, per outdoor unit:                    [D016][D020]
      the outdoor unit is wanted running if ANY room on it wants it;
      dry counts as running
      a start within 5 minutes of its last stop     refused: hold
      a stop within 10 minutes of its start         refused: keep running;
                                                    blind and fan choices
                                                    stand
      stops made by the user (Automatic control off, Leave) are not
      held                                                     [D088][D089]

  10. TARIFF CONSTRAINTS                                               [D034]
      any constraint declared for now is held absolutely; one this
      component does not know is reported, never dropped

  OFF commands the unit off; NONE sends nothing at all                [D017]

  NOT DELIVERED:                                                       [D054]
      IF any rule above changed, held or refused the step
          tell Control (its integral holds); RECORD which rule

GIVE BACK: the allowed step, warnings due, and the reason for each change
```

### Module 8 · Act

Act is the only part that writes to anything, and it records every command it sends [D005][D052].

**What it does:**

- **Compares with the unit's live state** and sends only what differs. A change made at the wall is put back on the next cycle while Automatic control is on [D007].
- **Sends** to climate entities and covers only, to every head in the room [D005][D020].
- **Records** every mode and setpoint command in the Home Assistant log, and keeps the last 200 [D052].
- **Carries the user's own commands** from the room's controls while Automatic control is off [D049].

**How it enables the intent:**

- **I6.** A change to a unit with no line in the log was not sent by this component [D052].
- **I1.** What was actually sent goes back to the model, so the filter learns from the real command [D081].

#### act.py

```text
FILE act.py — send the allowed step, and record it

DESIGN VALUES
  commands kept in memory .............. the last 200                  [D052]
  a blind is moved when it is off by ... more than 5 percentage points [Set]

EVERY CYCLE, per room, given the allowed step:
  IF the step is NONE       send nothing; stop                         [D017]
  FOR EACH head in the room:
      read its live mode, setpoint, fan speed and vane                 [D007]
      IF the step is OFF
          IF the head is not off          set mode off
      OTHERWISE
          IF the mode differs             set the mode
          IF the setpoint differs by a step or more
              one setpoint                set it, as the value whose
                  conversion by Home Assistant lands on the unit's own
                  grid, or a float above it, never below; at the top of
                  the unit's range a float below, never above      [D051][HA]
          a setpoint Home Assistant shows in whole degrees of another unit,
          coarser than the unit's step, is as wanted only within half a
          shown degree, and is sent once when nothing has been sent; a
          value other than the one last sent is sent; what was sent is
          forgotten while the room is not run                   [D007][HA]
              a range                     set setpoint - 1.0 to + 1.0   [D024]
          IF the fan speed differs        set the fan speed
          IF vanes are automatic and the vertical vane differs   set it [D049]
          (a horizontal vane is never moved by the component)         [D049]
      skip anything already as wanted, both in memory and live         [D007]
      FOR EACH command actually sent:                                  [D052]
          log at INFO: room, head, mode, setpoint, rate needed, room
          temperature, centre
          add it to the last-200 list
  FOR EACH blind the step moves:
      IF its position differs by more than 5 points   set the position [Set]
  IF a call fails
      log it; the room's other commands still go; nothing is retried
      until the next cycle                                            [Prop]

WHEN the user changes a room control (mode, setpoint, fan, vane):     [D049]
  IF Automatic control is on (or, for a vane, Automatic vane control)
      refuse, with a reason shown to the user
  OTHERWISE
      send it to every head, checked against the stored profile;
      record it, marked as the user's                                 [D052]

GIVE BACK: the commands sent, to Model and Evidence                    [D081]
```

### Module 9 · Evidence

Evidence says why each room is doing what it does, and shows as numbers whether the room is being learned and kept comfortable [D006].

**What it does:**

- **The decision trace**, on each room's mode sensor [D006].
- **The scorecard**, as sensors per room [Prop].
- **Spoken warnings**, only when true, only in a room the component runs, never in a sleeping room [D069][D071][D077].
- **Repairs** for what the user must fix [D034][D043][D046][D069].

**How it enables the intent:**

- **I6.** Every decision says why [D006].
- **I3.** Getting better is a published number: forecast error by week [Prop].

#### evidence.py

```text
FILE evidence.py — the trace, the scorecard, warnings and repairs

TRACE, every cycle, per room (the mode sensor's attributes):           [D006]
  state: the band rule in force (Section 7); action: off, rest, dry,
  cool, heat
  band in force and the rule that chose it; its centre in index and C
  comfort index now, and where it sits in the band
  who is in charge, and the other's proposal                         [Prop]
  the plan: its 12 actions, the index it forecasts, its time outside
  the band and each term of J; and for each other first action, the
  best plan that starts with it and why it lost                  [D006][Prop]
  the PID: e, P, I, D; Kp, Ti, Td; delay and lag                     [Prop]
  model: every figure and its uncertainty, the mass estimate,
         forecast error, TRUSTED or not, the sensor's learned noise    [Prop]
  precool on or off
  Economics: allowance, ceiling or "no limit"                         [D096]
  Limits: each rule that changed the step
  compressor state and which source answered
  stale feeds; free-cooling advice; condensation risk          [D030][D073]

SCORECARD, per room:                                                  [Prop]
  learning   EVERY WEEK: the week's average one-hour forecast error
  comfort    EVERY DAY: minutes the index was in band while occupied /
             minutes occupied
  starts     EVERY DAY: compressor starts / hours the compressor ran
  centre     EVERY DAY: share of occupied time within 0.3 of the centre
  cost       EVERY DAY: the sum over intervals of (learned draw x time
             running) x the interval's price
  each a sensor with a state class (measurement; total for cost, the only
  one Home Assistant allows for money), so Home Assistant keeps its
  long-term statistics                                       [HA][R15]

WARNINGS, when Limits or Delivery says one is due:
  IF Automatic control is off, the room is waiting for its unit to
     report, or the room is asleep
      drop it; say nothing                                            [D071]
  IF it says the unit is running or turning off, and the unit was not
     on at the start of this cycle
      drop it                                                         [D077]
  IF "turning off" is held by the minimum run
      keep it until the off is actually sent                          [D077]
  OTHERWISE
      tts.speak, target = the house's speech engine,
      media_player_entity_id = the room's speakers               [D069][HA]
  IF speaking fails for any reason
      log it once; the rest of the cycle carries on                   [D069]

REPAIRS, raised when their cause appears and cleared when it goes:
  a tariff constraint this component does not know                    [D034]
  the tariff cannot be read                                           [D004]
  a room whose temperature or humidity sensor no longer exists, or is
    the stand-in Home Assistant keeps once no integration provides it;
    not while the sensor's own integration is setting up, retrying or
    reloading, nor before Home Assistant has started; raised once
    it has been gone 15 minutes; an integration disabled or failed
    for good is not coming, so its sensor is gone          [D046][Set]
  no speech engine, or several and none chosen                        [D069]
  the solar forecast cannot be read                                   [D061]
  the grid sign contradicted 10 readings in a row                     [D038]
  grid import in a no-import window, naming the kWh                   [D043]
  two rooms configured on one climate entity (both locked out)        [D021]
  a room's filter has made no correction for 24 hours, naming the reading
  that stopped it                                                    [Prop]
  each repair says what to fix; none is raised for something the user
  cannot act on                                                       [R18]

LOST AND REGAINED, per room:                                          [R18]
  WHEN a required reading (room temperature or humidity, or a head's
       climate entity) becomes missing:
      log at INFO once, naming it; the room's sensors that depend on it
      become unavailable
  WHEN it returns: log at INFO once; nothing in between
```

### Module 10 · Memory

Memory keeps what each room has learned, and every choice its user has made, across restarts and upgrades [D031][D064].

**What it does:**

- **Saves learned state** on one pending timer, at most every 5 minutes. It saves user choices within 5 seconds, and writes everything when the component unloads [D031][D064]. A waiting save is never moved later: Home Assistant moves a waiting save later when a later one is asked for (helpers/storage.py, 2026.10.4), so a learned save is not asked for while any save is waiting [HA].
- **Restores** each room's filters, user choices, occupancy and unit profile before the first cycle [D049][D064][D068][D048].
- **Discards** any stored number that is not a finite number, and relearns it [D031].
- **Seeds** a new room by running its filter over the recorder's history [Prop]. The recorder keeps 10 days unless the user has changed it [HA].

**How it enables the intent:**

- **I3.** What a room has learned is the filter's state and uncertainty. It is kept, so a restart, a season or an upgrade never throws it away [I3][D031].

#### memory.py

```text
FILE memory.py — what survives

WHERE: Home Assistant storage, one file with a version number:      [Prop]
       .storage/abode_hvac_coordinator
       RL policy files beside it, one per room
       no records of readings are kept: the filter's state is what
       was learned from them                                         [Prop]

KEPT, per room:
  the heat filter: its 27 numbers and 27 x 27 uncertainty; the sensor
  step q; its learned noise R and the running average v             [Prop]
  the moisture filter: its state and uncertainty                     [Prop]
  the unit: U_min, draw by effort per outdoor
  unit                                                         [D028][Prop]
  trust: the last 48 one-hour errors, 14 days of six-hour errors,
  weekly errors                                                      [Prop]
  the controllers: who is in charge and since when, the arbiter's last
  14 days of scores                                                  [Prop]
  NOT the PID integral                                               [D031]
  the user's choices: Automatic control, Automatic vane control,
  Comfort mode, Leave, Precool and its ready time  [D049][D058][D088][I8]
  occupancy; the unit's capability profile                     [D068][D048]

DESIGN VALUES
  learned state saved at most every ............ 5 minutes              [D031]
  user choices saved within ................... 5 seconds              [D064]

AT START-UP, before the first cycle:                                  [D049]
  read the file
  FOR EACH room: restore everything above
  FOR EACH stored number
      IF it is not a finite number      discard it; it is relearned   [D031]
  IF the file is an older version     convert it; never discard learning [Prop]

WHEN the model learns something:                                      [D064]
  IF no save of either kind is waiting
                                    schedule one for 5 minutes from now
  (a waiting save is never pushed later: a learned save asked for after
   a choice would move the choice's save out to 5 minutes)

WHEN the user changes a choice:      save within 5 seconds; an earlier
                                     time replaces a later one, so this
                                     also brings a waiting learned save
                                     forward                         [D064]
WHEN the component unloads or reloads: write everything now           [D064]
WHEN a room is removed:                remove its stored state         [D031]

SEED, when a room is first set up (and from the options menu):        [Prop]
  FOR the room's temperature and humidity, the outdoor sensors, solar,
      house load, and each head's climate entity with its attributes:
      read every recorded row, attribute-only updates included (a
      unit's hvac_action, the forecast's cloud cover), with
      get_significant_states and significant_changes_only off, inside
      the recorder's executor                                        [HA]
  turn it into 5-minute steps exactly as the live path does
  run the filters over them in time order, each row at the time it was
  written (the later of its last change and its last report); the
  controllers do not act
  RECORD how many days were replayed
```
