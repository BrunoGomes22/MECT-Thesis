
# srsRAN compatible RUs

All the information stated below was extracted directly from srsRAN Project docs (https://docs.srsran.com/projects/project/en/latest/tutorials/source/oranRU/source/hw_integration.html)

#### Foxconn RPQN-(48/78)00(E/I)

- The different model numbers of the RPQN family of devices imply distinct functionalities:
	- With and without external antenna (the E letter at the end stands for external antenna and the I for internal).
	- For different bands (the 78 means n78 for Europe and 48 means band n48 for US markets).
	- There is also the “xx01” series that apparently has some further differences in RF characteristics.
- RPQN O-RU specs:
![[Pasted image 20260603114139.png]]
![[Pasted image 20260603114502.png]]
- [Manual](https://manuals.plus/foxconn/rpqn-o-ru-local-5g-nr-system-manual)
- To purchase a Foxconn RPQN O-RU (Open Radio Unit) for your private 5G network, you should contact official enterprise 5G partners like [Software Radio Systems (SRS)](https://srs.io/software-radio-systems-oru-integrations/) or directly engage with Foxconn's enterprise sales division
#### Picocom PC802SCB
- **PC802SCB is not a deployment ready commercial grade O-RU**,  but many commercial O-RUs are based on the PC802 platform
- it is used in one of srsRAN's tutorials
- The **PC802SCB** supports the following small cell architecture configurations as defined by 3GPP, O-RAN Alliance and Small Cell Forum:
	- Split 0 Integrated Small Cell (gNB) (-1E)
	- Split 2 Distributed unit (gNB-DU)
	- Split 6 Radio Unit (S-RU)
	- Split 7.2 Radio Unit (O-RU)
- **Key features**:
	- PC802 SoC Silicon subsystem
		- 8GB 32-bit interface LPDDR4 SDRAM
	- NPU NXP LS1046A subsystem
		- LS1046AXN8Q1A without encryption (-1)
		- LS1046ASE8T1A with encryption (-1E)
	- ADI ADRV9029BBCZ 4T4R RFIC subsystem
	- 650MHz to 3.5GHz TX/RX frequency range (Maximum recommended frequency)		
	- Synchronisation and clocking functions using on-board GNSS receiver or IEEE 1588
	- Debug boot mode support
- **Key External Interfaces**:
	- SFP1 cage for 10GE SFP+ optical LC interface for gNB, split 2 and 6 use cases
	- SFP2 cage for SFP28 optical LC 10/25GE interface for O-RAN open fronthaul eCPRI
	- GNSS Antenna port
	- FR1 RF ports to test equipment or external RFFE
		- 4 TX and 4 RX SMA
		- 4 observation paths ORX SMA
	- RFFE control interface (30-pin header)
	- Test and Debug ports including
		- 1G Ethernet NPU debug port (RJ45)
		- Micro USB Siemens/Mentor Tessent UDAgent debug port
		- Micro USB console port to PC802 UART

**Ordering information**

| Part number | Product Name/Description                           | Details/ECCN             |
| ----------- | -------------------------------------------------- | ------------------------ |
| PC802SCB-1  | PC802 Small Cell Development Board (no encryption) | ES quantities, 5A991.b.4 |
| PC802SCB-1E | PC802 Small Cell Development Board (encryption)    | ES quantities, 5A002a3   |

For additional information, examine the [manual](https://picocom.com/wp-content/uploads/2024/03/PC-002621-DS-8-PC802_Small_Cell_Development_Board_Product_Brief-2024.pdf)

#### LITEON FlexFi FF-RFI078I4
![[Pasted image 20260603160324.png]]

#### VVDN
(no specific RUs are specified by srsRAN)
https://www.vvdntech.com/adaptive-compute-and-comms/5g-oran-radio-unit
#### Benetel
- Benetel 550 (https://benetel.com/ran550/)
- [Datasheet](https://cdn.prod.website-files.com/665f1b4b8eba5127ca955ccd/66f55ffe2c1bda1b594f04a4_RAN550_Datasheet_v1.9.pdf)

- Benetel 650 (https://benetel.com/ran650/)
- [Datasheet](https://cdn.prod.website-files.com/665f1b4b8eba5127ca955ccd/66f5617093fe23afc928b998_RAN650_n78_Datasheet_v1.3.pdf)

