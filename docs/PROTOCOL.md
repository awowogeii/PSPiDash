# Hondata Binary Protocol — Ground Truth

Permanent reference for the S300 V3 datalog protocol as implemented in `s300d/`.
Transcribed verbatim from the project specification. Do NOT invent commands,
channel IDs, or scaling factors that are not in this document.

Transport: Bluetooth RFCOMM (SPP). It is a **byte stream**, not a datagram service. One recv() may return a partial packet or multiple packets.

Every packet — sent and received — begins with a 4-byte header:

    byte 0: protocol   (always 0x00, PROTOCOL_HD)
    byte 1: command ID
    bytes 2-3: uint16 little-endian total packet size, INCLUDING these 4 header bytes

Every command is exactly 4 bytes: `{0x00, <command_id>, 0x04, 0x00}`

## Commands used in this project (all read-only)

| ID   | Name                     | Response payload (after header)                          |
|------|--------------------------|----------------------------------------------------------|
| 0x02 | DL_DeviceInfo            | uint8 DeviceType + uint8 IgnitionOn. DeviceType: 0xC0=S300, 0xC1=KPro, 0xC2=FlashPro |
| 0x30 | DL_GetDatalogInfo        | uint16 ChannelCount + uint16 PacketSize                  |
| 0x31 | DL_GetDatalogChannelIDs  | array of {uint16 ChannelID, uint8 SizeAndType}           |
| 0x35 | DL_GetDatalogPacket      | packed binary channel data                               |

SizeAndType byte: high 2 bits = storage size (mask 0xC0), low 6 bits = data type (mask 0x3F).

| Size code | Name     | Bytes                   |
|-----------|----------|-------------------------|
| 0x40      | CS_BYTE  | 1                       |
| 0x80      | CS_WORD  | 2 (little-endian)       |
| 0xC0      | CS_DWORD | 4                       |

Datalog packet layout: channels appear in the exact order returned by 0x31, packed with no padding. Compute each channel's byte offset by walking the 0x31 array and accumulating its storage size.

## Data types and conversions (raw -> engineering units)

| Code | Name           | Conversion             | Unit    |
|------|----------------|------------------------|---------|
| 0x01 | CT_BIT         | bool (0=off, 1=on)     |         |
| 0x02 | CT_NUMBER      | raw                    |         |
| 0x03 | CT_RPM         | raw * 0.25             | rpm     [SEE CALIBRATION NOTE] |
| 0x04 | CT_SPEED       | raw * 0.01             | kph     |
| 0x05 | CT_MBAR        | raw / 10.0             | kPa     |
| 0x06 | CT_KPA         | raw * 0.5              | kPa     |
| 0x07 | CT_TPS         | (raw / 2.0) - 10.0     | percent |
| 0x08 | CT_INJ         | raw / 1000.0           | ms      |
| 0x09 | CT_IGN         | (raw - 20.0) / 2.0     | degrees |
| 0x0B | CT_RETARD      | raw / 2.0              | degrees [SEE CALIBRATION NOTE] |
| 0x10 | CT_TEMP        | raw                    | degF (convert to degC at the presentation layer) |
| 0x11 | CT_PCT         | raw / 2.56             | percent |
| 0x12 | CT_PCT_SIGNED  | (raw - 128) / 1.28     | percent |
| 0x13 | CT_PCT_CHG     | raw * 0.01             | percent |
| 0x16 | CT_MASSFLOW    | raw                    | g/s     |
| 0x18 | CT_5V          | raw * 5.0 / 256.0      | volts   |
| 0x19 | CT_19V         | 6.0 + (raw / 20.0)     | volts   |
| 0x1E | CT_LAMBDA      | raw / 32768.0          | lambda  |
| 0x20 | CT_BAR         | raw                    | bar     |
| 0x21 | CT_MM          | raw                    | mm      |
| 0x22 | CT_GFORCE      | raw                    | g       |
| 0x23 | CT_SIGNED      | signed raw             |         |
| 0x24 | CT_SIGNED100   | signed raw * 0.01      |         |

CALIBRATION NOTE: the Hondata spec text and its own worked example disagree on CT_RPM and CT_RETARD. Implement the values above, but make every scaling factor overridable from config.yaml under a `scaling_overrides` key, keyed by type name. Both marked types must be verified against a physical tachometer / known timing value.

## Channel IDs required for this project

| ID     | Name               | ID     | Name               |
|--------|--------------------|--------|--------------------|
| 0x0100 | RPM                | 0x0101 | Speed              |
| 0x0102 | Gear               | 0x0110 | MAP                |
| 0x0111 | MAPVoltage         | 0x0120 | TPS                |
| 0x0130 | InjectorDuration   | 0x0132 | InjectorDuty       |
| 0x0140 | IgnitionAdvance    | 0x0141 | IgnitionDwell      |
| 0x0150 | IAT                | 0x0160 | ECT                |
| 0x0170 | BarometricPressure | 0x0180 | BatteryVoltage     |
| 0x0200 | VtecSpool          | 0x0201 | VtecPressure       |
| 0x0320 | Lambda             | 0x0321 | CorrectedLambda    |
| 0x0322 | TargetLambda       | 0x0328 | WidebandVoltage    |
| 0x0329 | WidebandLambda     | 0x0400 | KnockLevel         |
| 0x0402 | KnockThreshold     | 0x0410 | KnockRetard        |
| 0x0420 | KnockCount         | 0x0710 | RevLimiter         |
| 0x0711 | IgnitionCut        | 0x0712 | BoostCut           |
| 0x0713 | LaunchCut          | 0x0715 | ShiftCut           |
| 0x0730 | BoostControlDuty   | 0x0900 | AnalogInput1       |
| 0x0901 | AnalogInput2       |        |                    |

Unknown channel IDs MUST be decoded (using their SizeAndType) and passed through as `unknown_0xNNNN`, never dropped and never allowed to break offset calculation.

## Known-good capture

Channel list `{0x0100:word/CT_RPM, 0x0101:word/CT_SPEED, 0x0102:byte/CT_NUMBER, 0x0110:word/CT_MBAR, 0x0120:byte/CT_TPS}`,
payload bytes `20 12 00 00 01 23 01 55` decode to RPM=1160, Speed=0.0 kph, Gear=1, MAP=29.1 kPa, TPS=32.5%.
