#### Chip-8 emulator
##### Chip-8 overview
Chip-8 is an interpreted programming language developed by Joseph Weisbeck in 1802, which initially ran on a microprocessor. 
Chip-programs can be run either on a virtual machine, through an interpreter, with less than 50 instructions.

###### Components of a Chip-8 virtual machine
**Memory** - they typically contain 4096 (0x1000) 8-bit memory locations, hence the name Chip 8. 

**Registers** - the VMs have 16 8-bit data registers; VF registers are carry flags, while in subtraction they're 'no borrow' flags.

**Timers** - Chip-8 has two timers, a delay timer & a sound timer, that count down at 60 hertz till they reach .

**Graphics** - visuals are drawn on-screen with sprite pixels.

**Input** - originally done with a hex keyboard that has 16 keys ranging 0 to F, some are used for directional input, while some are used to skip instructions.


##### Hardware
**Input (game controls)**
- Coming from a 16 button keyboard, we can use python to store & check key input states in each cycle
```
self.key_inputs = [0] * 16
```

**Output (graphics)**
- We can represent the 64x32 display as an array of pixels that can be either on or off.
```
self.display_buffer = [0] * 32 * 64
```


**CPU & Memory**
Memory
- The memory has a maximum capacity of 4096 bytes, including storage for the actual interpreter, fonts & the host.
```
self.memory = [0] * 4096
```

Registers
- The chip-8 has 16 8-bit registers, usually referred to as Vx, where x is the register number.
	- This is used to store the results of operations.
- The last register, Vf, is used for flags.
- We store the register values with python as:
```
self.gpio = [0] * 16   # 16 zeroes
```
- We'll also need timer registers, which cause delays by decrementing to 0 for each cycle
```
self.sound_timer = 0
self.delay_timer = 0
```
- There's also a 16 bit index register
```
self.index = 0
```
- The program counter is also 16-bit.
```
self.pc = 0
```
- The stack pointer is just a list that we can pop/append.
	- The stack pointer stores the address of the topmost stack, which contains up to 16 elements at any given time.
```
self.stack = []
```
##### Walkthrough

**I/O**
-  I used Pyglet for I/O, 
	- Pyglet is a module that wraps around OpenGL boilerplate to easily configure video graphics.

- Since pyglet doesn't work with threads, we'll subclass pyglet's window class and run the Chip-8 interpreter from there.
	- Now we don't have to make our own input handlers, we just need to override the keyboard input methods.
```
class cpu (pyglet.window.Window)
```

```
def on_key_press(self, symbol, modifiers)
def on_key_release(self, symbol, modifers)
```


**Initialization**
```
def initialize(self): 
    self.clear() 
    self.memory = [0]*4096 # max 4096 
    self.gpio = [0]*16 # max 16 
    self.display_buffer = [0]*64*32 # 64*32 
    self.stack = [] 
    self.key_inputs = [0]*16   
    self.opcode = 0 
    self.index = 0 
    self.delay_timer = 0 
    self.sound_timer = 0 
    self.should_draw = False 
    self.pc = 0x200 
    ...
```
- Sets all registers to zero, resets all key inputs & the program counter.
- It then loads the ROM, performs each cycle and updates the display accordingly.
- The should_draw flag lets us update the display when necessary/


**Program counter**
- The program counter is the address that points to the memory location of an instruction to be executed sequentially.
	- Since the interpreter occupies the first portion, we have to point it to the offset (0x200) in the cycle method. 


**Graphics**
- Chip8 uses sprites for graphics. 
	- Sprites are a set of bits that indicate refer to active or inactive pixels. 
- Chip8 allocates sprites for the 16 hexadecimal digits. Since each font character is 8x5 bits, we need to allocate 5 bytes per character.
```
def initialize(self): 
    .... 
    i = 0 
    while i < 80: 
      #loads the 80-char font set 
      self.memory[i] = self.fonts[i] 
      i += 1
```


**ROM**
- We can load ROM into memory by opening them as binary, like this:
```
def load_rom(self, rom_path):
    log(f"Loading {rom_path}..") 
    binary = open(rom_path, "rb").read()
    i = 0
    while i < len(binary):
      self.memory[i+0x200] = ord(binary[i])
      i += 1
```


**Cycle()**
```
def cycle(self): 
    self.opcode = self.memory[self.pc]


    self.pc += 2
  
    # decrements the timers 
    if self.delay_timer > 0:
      self.delay_timer -= 1
    if self.sound_timer > 0:
      self.sound_timer -= 1
      if self.sound_timer == 0:
        # Plays a sound
```

```
    self.vx = (self.opcode & 0x0f00) >> 8
    self.vy = (self.opcode & 0x00f0) >> 4
    self.pc += 2


    # retrieves & executes opcodes
    extracted_op = self.opcode & 0xf000
    try:
      self.funcmap[extracted_op]() # calls the associated method 
    except:
      print f"Unknown instruction: {self.opcode}"
```
- The opcode refers to a pending executable.
- Each opcode is 2 bytes long, we read the binary line by line and execute the corresponding actions.
- The program counter points to the current opcode which needs to be processed.
	- After the opcode is retrieved & processed, we increment the program counter by 2 bytes and repeat the cycle.
	- Unfortunately Python doesn't have switch statements like C, so we have to use compound if-else conditions.