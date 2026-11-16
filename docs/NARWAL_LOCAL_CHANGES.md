# Changes to narwal_local

`custom_components/narwal_local` started as cvette/narwal-ha 0.12.0 (MIT).
The main changes, taken from the private setup's commit history:

- Dock tasks: wash mop, stop dock bag drying, dock bag life sensor.
- Room names as the app shows them.
- Freo 20 app settings as entities: carpets, obstacle avoidance, corner cleaning,
  mop rewash, mop washing frequency, mop drying, auto-empty frequency and
  **Auto-Empty Level** (quiet/strong), default dock button mode, Freo Mind
  suction / mopping humidity / coverage precision, hot water mop wash, dust box
  and bag disinfection, child lock, pet mode, plateau mode, auto power off,
  do-not-disturb and off-peak charging times.
- Mop rewash area mapping fixed (3 = every 15 m²); coverage precision named
  "Intensive" as in the app.
- Result of the last task, and a diagnostic "Robot capabilities" sensor with the
  robot's feature flags.
- Virtual walls: services `narwal_local.add_virtual_walls` and
  `narwal_local.delete_virtual_walls`, using the app's own virtual-wall map
  operation (map edit payload field 13).

The window and `narwal_mapa` depend on these entities and services. They have
not been sent upstream; splitting them into pull requests for cvette/narwal-ha
is possible if that is useful.
