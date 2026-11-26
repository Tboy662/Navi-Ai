# Minecraft guide for Navi
Each "## " section is one topic for mc_guide. Edit freely: Navi reads this file every time.

## crafting
Use mc_craft(item, count): it knows the recipes below, makes missing planks, sticks and a crafting table itself, and
does all the moving. Everything below is for things mc_craft doesn't know.
Slot numbers (from mc_container):
- Your inventory screen: [0] result, crafting grid [1] [2] on top, [3] [4] below. Armor [5]-[8], inventory [9]-[35], hotbar [36]-[44], offhand [45].
- Crafting table: [0] result, grid [1] [2] [3] top row, [4] [5] [6] middle row, [7] [8] [9] bottom row. Your inventory [10]-[36], hotbar [37]-[45].
- Furnace: [0] what to smelt, [1] fuel, [2] result.
- Chest: chest [0]-[26], then your inventory and hotbar.
How to craft: put each ingredient in its grid slot with mc_move_item(slot, grid slot, 1); the reply says what the result slot now holds. Then mc_shift_click(0) takes it. If the result is empty, the pattern is wrong.
A 2x2 recipe works in the inventory grid; a 3x3 one needs a crafting table (place it, then mc_use_block on it).
Recipes (positions on a 3x3 grid: TL TM TR / ML MM MR / BL BM BR):
- Planks: 1 log in any slot -> 4 planks. Works in the 2x2 grid.
- Sticks: 1 plank on top of another (TM over MM, or [1] over [3] in the inventory) -> 4 sticks.
- Crafting table: 4 planks filling a 2x2 square -> 1 table.
- Torch: coal or charcoal on top of a stick -> 4 torches.
- Pickaxe: 3 of the material across the top row (TL TM TR), sticks at MM and BM. Wood, cobblestone, iron ingots or diamonds.
- Axe: material at TL TM ML, sticks at MM BM.
- Shovel: material at TM, sticks at MM BM.
- Sword: material at TM and MM, stick at BM.
- Hoe: material at TL TM, sticks at MM BM.
- Furnace: 8 cobblestone around the edge, middle empty.
- Chest: 8 planks around the edge, middle empty.
- Door: 6 planks filling two columns of three -> 3 doors.
- Ladder: 7 sticks in an H (both side columns plus the middle) -> 3 ladders.
- Bed: 3 wool across the top row, 3 planks across the middle row.
- Bread: 3 wheat in a row.
- Iron, gold, copper ingots: smelt the raw ore in a furnace (fuel: coal, charcoal, planks or logs). One item takes 10 seconds.

## mining
- Pickaxe tiers: wood < stone < iron < diamond < netherite. Stone and coal need a wooden pickaxe or better; iron, copper and lapis need stone or better; gold, diamond, redstone and emerald need iron or better; obsidian and ancient debris need diamond. With a weaker pickaxe the block breaks slowly and drops nothing.
- Axes cut wood fastest, shovels dig dirt, sand, gravel and snow.
- Where ores are (y heights): diamonds from y 16 down to -64, most around -59. Redstone below y 15, most near the bottom. Gold below y 32, most around -16 (also high up in badlands). Iron everywhere but most around y 16 and in tall mountains. Copper around y 48. Lapis around y 0. Coal is common from y 0 up, most around 95.
- mc_find_block(name="diamond_ore") also finds deepslate versions. Search radius up to 48 shows ores hidden in walls.
- Safe digging: never dig straight down under yourself. Dig a staircase (look down about 45 degrees, mine 2 blocks, step forward, repeat). Below y -50 watch for lava lakes. Block lava with cobblestone.

## survival
- First day: chop a tree, make planks, a crafting table, a wooden pickaxe, then mine stone for stone tools. Find food (animals, bread).
- Night on the status clock is about 19:00 to 05:30. Hostile mobs spawn in the dark: be in a shelter, or wall yourself in, or sleep in a bed (mc_use_block on it) to skip the night.
- Torches stop mobs spawning near them.
- Food: below 6 food you can't sprint, at 0 you lose health. Health only heals by itself when food is 18 or more. Cooked meat and bread are good food. Cook raw meat in a furnace.
- If health is low: back off, eat, and avoid fighting.

## mobs
- Zombies: slow, hit them with a sword. Skeletons shoot arrows: close in fast or take cover; they burn in daylight. Creepers explode about 1.5 seconds after getting close: hit once, step back, repeat. Spiders are calm in daylight. Endermen get angry if you look at their face. Drowned live in water.
- Animals: cows (beef, leather), pigs (pork), sheep (wool, mutton), chickens (chicken, feathers, eggs).
- mc_attack waits for full-strength swings and stops when the target dies. A sword does much more damage than a fist.

## building
Making it look good:
- Frame the build: logs or a different block as corner pillars and along the floor and roof lines.
- Mix materials: walls of planks or stone bricks, a stone or cobblestone foundation, glass panes for windows.
- Roofs: stairs or slabs, stepped in by one block each layer, overhanging the walls by one block, ending in a ridge.
- Depth: window sills, shutters (trapdoors), fences, flower pots, lanterns hanging under eaves.
- Lighting: lanterns or torches inside and at the door.
- Palettes: oak + cobblestone (classic), spruce + stone bricks (rustic), birch + quartz (modern), dark oak + deepslate bricks (medieval), sandstone + terracotta (desert).
Blueprint tips for mc_build:
- Scan the spot first to see the ground height.
- The bottom layer is the floor or foundation on top of the ground (y = your feet y when you stand on it).
- Walls 4 or 5 high, a door gap of 2 high made with _ , windows on the second and third wall layer.
- Build in parts with names: foundation and floor, walls, roof, details. About 600 blocks per call at most.
- Leave hollow insides as _ so old blocks get cleared.
- Give exact states for blocks that face a way: K=oak_stairs[facing=south,half=bottom], D=oak_door[facing=north,hinge=left],
  O=oak_log[axis=x]. Only the bottom half of a door or the foot of a bed is needed; the rest comes with it.

## redstone
Directions: north is -z, south is +z, west is -x, east is +x. In a blueprint layer, rows go north to south and letters
west to east, so "facing=east" points to the right along a row.
Give every directional part an exact state in the legend; mc_build then places it facing that way and clicks repeaters,
comparators, levers and note blocks to the setting given. mc_scan(states=True) shows what was really built.
How parts face (the facing value):
- repeater and comparator: facing is the side the signal comes IN from; it goes out the opposite side. A signal
  travelling east needs repeater[facing=west]. delay=1 to 4 (each 0.1 s). Comparator mode=compare or subtract; it also
  reads side inputs and how full a container behind it is.
- redstone_wall_torch: facing points away from the block it hangs on (facing=east hangs on the block to its west). It
  is on until that block gets powered (a NOT gate). A torch powers the block above it and dust next to it. On the
  ground use redstone_torch.
- observer: facing is the side it watches; it sends a short pulse out of its back when that block changes.
- piston and sticky_piston: facing is the way it pushes (facing=up pushes the block above). It needs a free block in
  front to extend into. A piston head never needs placing.
- dispenser and dropper: facing is the side items come out. hopper: facing is where items go (down or sideways, never up).
- lever and buttons: face=floor, wall or ceiling; on a wall, facing points away from the wall. Keep levers
  powered=false in a blueprint and switch them with mc_use_block afterwards (levers that should be on are placed last).
- redstone_wire (dust) connects by itself. Power drops by 1 per block, so put a repeater every 15 blocks at most. Dust
  powers the block it sits on and the block it points into. It needs a solid block under it.
- redstone_lamp lights when powered. note_block[note=0..24] plays a pitch set by clicking; the block under it sets the
  instrument. target, daylight_detector[inverted=true/false], redstone_block (always on), tripwire_hook.
Working circuits (each built on a floor, a row of stone below them):
- Lever to lamp with a repeater (signal goes east):
  legend L=lever[face=floor,facing=east,powered=false], W=redstone_wire, R=repeater[facing=west,delay=1], P=redstone_lamp
  layer: LWRWWP
- NOT gate (lamp is on while the lever is off): a block B with a torch on its far side.
  legend L=lever[face=floor,facing=east,powered=false], W=redstone_wire, B=stone, T=redstone_wall_torch[facing=east], P=redstone_lamp
  layer: LWBTWP
- Piston that lifts a block: L=lever[face=floor,facing=east,powered=false], W=redstone_wire, R=repeater[facing=west,delay=1],
  P=sticky_piston[facing=up], X=oak_planks. Layer 1: LWRP, layer 2: ...X
- Clock that pulses forever: two observers watching each other, observer[facing=east] then observer[facing=west]
  in a row, with dust or a lamp behind one of them. Break one to stop it.
- Door with 2 sticky pistons: pistons facing toward the doorway on both sides, the door blocks in front of them, and
  a lever or pressure plate powering both (dust under the floor or a block behind each piston).
- Longer signal: dust, then a repeater facing the incoming side, then more dust. Lock a repeater by powering it
  from the side with another repeater.

## nether
The road to beating the game: iron tools and armor, a bucket, flint and steel, then the Nether for blaze rods, ender
pearls from endermen, eyes of ender, the stronghold, and the dragon in the End.
Before going: iron (or diamond) armor and sword, a bow and 64+ arrows, food, 64 cobblestone for bridging, a pickaxe,
and one piece of gold armor (golden_helmet) so piglins leave you alone.
- Portal: 10 obsidian and flint and steel (iron ingot + flint from gravel). mc_build_portal(x, y, z, along) builds
  and lights it next to you. Obsidian: mine it with a diamond pickaxe (about 10 s each), or pour water on still lava
  to make it. Write down the portal's coordinates (mc_remember) so you can find the way back.
- Going through: mc_go_to into the portal block and stand still about 4 seconds (mc_move direction none). Check
  mc_status: dimension the_nether.
- Moving in the Nether: always mc_go_to with dig_and_build=True; it bridges over lava with cobblestone or netherrack.
  Never dig straight down. Ghasts shoot fireballs: shoot them with mc_shoot or get under cover.
- Fortress: dark nether brick buildings. mc_explore(direction, 300, "nether_bricks", True), trying east or west and
  north or south. Blazes spawn near spawners in it (mc_find_block "spawner").
- Blaze rods: kill blazes, with mc_shoot or mc_attack when they come close; they shoot fireballs, so keep health
  up. Get 7 or more rods (each makes 2 blaze powder; 12 eyes of ender need 6 rods).
- Ender pearls: kill endermen (mc_attack enderman). Overworld at night, or warped forests in the Nether. Need about
  12. Craft eyes: mc_craft("blaze_powder"), then mc_craft("ender_eye", 12).

## end
- Find the stronghold: back in the overworld, mc_throw_eye; walk about 100 blocks to the side of where it flew;
  throw again. It tells you roughly where the stronghold is. Go there with mc_go_to(x, 999, z, ...), throw again
  every few hundred blocks. When an eye goes down, the stronghold is under you: dig down with
  mc_go_to(x, y-30, z, dig_and_build=True), then mc_find_block("end_portal_frame", 48).
- The portal: 12 end_portal_frame blocks around a 3x3 hole over lava. Some already have eyes. Hold ender_eye and
  mc_use_block each empty frame (walk around it). When all 12 have eyes the portal opens. Step in with mc_go_to
  onto an end_portal block.
- The End: you land on an obsidian platform. Endermen everywhere: don't look at them. The dragon heals from the
  end crystals on top of the tall obsidian pillars, so destroy every crystal first: mc_shoot(target="end_crystal")
  from a distance (they explode). Two crystals sit in iron cages: pillar up next to that pillar with mc_go_to(...,
  dig_and_build=True), break the bars, and hit the crystal from a few blocks away by shooting it.
- Then the dragon: shoot it while it flies (mc_shoot target="ender_dragon", several shots), and when it lands on
  the bedrock fountain in the middle (around 0 64 0), run in and mc_attack ender_dragon. Back off from its purple
  breath. Keep eating and keep health high.
- When it dies, a portal opens in the fountain: jump in to see the end poem. That's beating the game.

## navi
- Reach is about 4.5 blocks. Get within 3 or 4 blocks before mining, using or placing.
- To gather blocks: mc_mine(block="oak_log", count=5) walks to each one, mines them one after another and picks up
  the drops. For one exact block: mc_look_at_block on it, then mc_mine(block="", count=1).
- To use a block (chest, door, bed): mc_use_block with its coordinates (walk within reach first).
- An item in your inventory but not the hotbar: mc_open_inventory, mc_to_hotbar(slot, hotbar), mc_close_menu.
- To place one block: pick it with mc_hotbar, look at the top of a ground block next to you with mc_look_at_block(x, ground y, z), then mc_use. For many blocks use mc_build.
- Dropped items are picked up by walking within a block of them.
- To wait (for smelting, or for day): mc_move(direction="none", seconds=10).
- Check mc_status after something goes wrong: an open menu, low health or night can explain it.
