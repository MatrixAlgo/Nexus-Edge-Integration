#!/usr/bin/env python3
"""Generate the D5_EdgeSync message flows (ACE 13 Toolkit .msgflow format).

    python3 ace/flows/gen_flows.py

Writes into ace/projects/D5_EdgeSync. Node attribute names and enum values were checked against
<ACE>/common/schemas/MessageFlow/MessageFlow.xsd on this server (ACE 13.0.9.0). The project can be imported
into the App Connect Toolkit (File > Import > Existing Projects into Workspace) to view/edit the flows.
"""
import os
from xml.sax.saxutils import quoteattr

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "ace", "projects", "D5_EdgeSync")
APP = "D5_EdgeSync"
CENTRAL = "http://127.0.0.1:8500"

NS = {"MQInput": "ComIbmMQInput.msgnode", "MQOutput": "ComIbmMQOutput.msgnode", "Compute": "ComIbmCompute.msgnode",
      "HTTPRequest": "ComIbmWSRequest.msgnode", "HTTPInput": "ComIbmWSInput.msgnode", "HTTPReply": "ComIbmWSReply.msgnode"}


def flow(name, nodes, conns, udps=(), note=""):
    used = sorted({NS[n[1]] for n in nodes})
    ns = " ".join('xmlns:%s="%s"' % (p, p) for p in used)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<ecore:EPackage xmi:version="2.0" xmlns:xmi="http://www.omg.org/XMI" %s '
           'xmlns:ecore="http://www.eclipse.org/emf/2002/Ecore" xmlns:eflow="http://www.ibm.com/wbi/2005/eflow" '
           'xmlns:utility="http://www.ibm.com/wbi/2005/eflow_utility" nsURI="%s.msgflow" nsPrefix="%s.msgflow">' % (ns, name, name),
           '  <eClassifiers xmi:type="eflow:FCMComposite" name="FCMComposite_1" nodeLayoutStyle="RECTANGLE">',
           '    <eSuperTypes href="http://www.ibm.com/wbi/2005/eflow#//FCMBlock"/>']
    for u, typ, dflt in udps:
        out += ['    <eStructuralFeatures xmi:type="ecore:EAttribute" xmi:id="Property.%s" name="%s" defaultValueLiteral=%s>' % (u, u, quoteattr(dflt)),
                '      <eType xmi:type="ecore:EDataType" href="http://www.eclipse.org/emf/2002/Ecore#//%s"/>' % typ,
                '    </eStructuralFeatures>']
    out.append('    <translation xmi:type="utility:TranslatableString" key="%s" bundleName="%s" pluginId="%s"/>' % (name, name, APP))
    out.append('    <composition>')
    for nid, kind, label, (x, y), attrs in nodes:
        a = " ".join("%s=%s" % (k, quoteattr(str(v))) for k, v in attrs.items())
        out += ['      <nodes xmi:type="%s:FCMComposite_1" xmi:id="FCMComposite_1_%d" location="%d,%d" %s>' % (NS[kind], nid, x, y, a),
                '        <translation xmi:type="utility:ConstantString" string=%s/>' % quoteattr(label),
                '      </nodes>']
    for i, (s, term, d) in enumerate(conns, 1):
        out.append('      <connections xmi:type="eflow:FCMConnection" xmi:id="FCMConnection_%d" targetNode="FCMComposite_1_%d" '
                   'sourceNode="FCMComposite_1_%d" sourceTerminalName="OutTerminal.%s" targetTerminalName="InTerminal.in"/>' % (i, d, s, term))
    out.append('    </composition>')
    if udps:
        out.append('    <propertyOrganizer>')
        for depth, (u, _, _) in enumerate(udps):
            ind = "      " + "  " * depth
            out += ['%s<propertyDescriptor groupName="Group.Basic" configurable="true" userDefined="true" describedAttribute="Property.%s">' % (ind, u),
                    '%s  <propertyName xmi:type="utility:TranslatableString" key="Property.%s" bundleName="%s" pluginId="%s"/>' % (ind, u, name, APP)]
        for depth in range(len(udps) - 1, -1, -1):
            out.append("      " + "  " * depth + "</propertyDescriptor>")
        out.append('    </propertyOrganizer>')
    else:
        out.append('    <propertyOrganizer/>')
    if note:
        out += ['    <stickyBoard>', '      <stickyNote location="20,320">',
                '        <body xmi:type="utility:ConstantString" string=%s/>' % quoteattr(note), '      </stickyNote>', '    </stickyBoard>']
    else:
        out.append('    <stickyBoard/>')
    out += ['  </eClassifiers>', '</ecore:EPackage>', '']
    path = os.path.join(OUT, name + ".msgflow")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("wrote", path)


def esql(module, mode="all"):
    return {"computeExpression": "esql://routine/#%s.Main" % module, "computeMode": mode}


CENTRAL_UDP = [("CentralBaseUrl", "EString", CENTRAL)]
PERSIST = {"persistenceMode": "yes"}

# 1. Vessel -> ACE REST ingest -> validation -> persistent MQ put by priority -> 202 (NOT a business ack)
flow("D5_EventIngest",
     [(1, "HTTPInput", "Receive_Vessel_Event", (20, 120), {"URLSpecifier": "/d5/v1/events", "messageDomainProperty": "JSON"}),
      (2, "Compute", "Validate_And_Route", (220, 120), esql("D5_ValidateAndRoute")),
      (3, "MQOutput", "Put_Persistent_Event", (440, 120), dict(PERSIST, destinationMode="list", transactionMode="no")),
      (4, "Compute", "Build_HTTP_Reply", (660, 120), esql("D5_BuildIngestReply")),
      (5, "HTTPReply", "Reply", (860, 120), {})],
     [(1, "out", 2), (2, "out", 3), (3, "out", 4), (4, "out", 5)],
     note="POST /d5/v1/events. Valid events: PERSISTENT put to D5.IN.<priority> (MQ priority 9/5/1, CorrelId=eventId) "
          "outside syncpoint so the message is durable before HTTP 202 is returned. Invalid events: parked on D5.ERROR, HTTP 400.")

# 2. MQ -> central business processing -> business acknowledgement (all under one MQ unit of work)
queues = [("D5.IN.CRITICAL", "Critical_Events"), ("D5.IN.NORMAL", "Normal_Events"), ("D5.IN.LOW", "Low_Events")]
nodes = [(i + 1, "MQInput", lbl, (20, 40 + i * 110), {"queueName": q, "messageDomainProperty": "JSON", "transactionMode": "yes"})
         for i, (q, lbl) in enumerate(queues)]
nodes += [(4, "Compute", "Prepare_Central_Call", (240, 150), esql("D5_PrepareCentralCall")),
          (5, "HTTPRequest", "Central_Processing", (460, 150),
           {"URLSpecifier": CENTRAL + "/internal/process-event", "httpMethod": "POST", "httpVersion": "1.1",
            "timeoutForServer": "15", "messageDomainProperty": "JSON"}),
          (6, "Compute", "Build_Business_Ack", (700, 60), esql("D5_BuildBusinessAck")),
          (7, "MQOutput", "Put_Business_Ack", (930, 60), dict(PERSIST, queueName="D5.OUT.ACK", transactionMode="yes")),
          (8, "Compute", "Handle_Central_Error", (700, 260), esql("D5_HandleCentralError")),
          (9, "MQOutput", "Park_On_Error_Queue", (930, 200), dict(PERSIST, queueName="D5.ERROR", transactionMode="yes")),
          (10, "MQOutput", "Put_Rejection_Ack", (930, 320), dict(PERSIST, queueName="D5.OUT.ACK", transactionMode="yes"))]
flow("D5_EventProcessor", nodes,
     [(1, "out", 4), (2, "out", 4), (3, "out", 4), (4, "out", 5), (5, "out", 6), (6, "out", 7),
      (5, "error", 8), (8, "out", 9), (8, "out1", 10)],
     udps=CENTRAL_UDP,
     note="Each MQ get, the central call and the ack put are one MQ unit of work. Central commits its ledger first and is "
          "idempotent on eventId, so a redelivery after a failure cannot create a second business transaction. "
          "HTTP 4xx = permanent rejection (parked on D5.ERROR + rejection ack). 5xx/timeout = rollback; MQ redelivers; "
          "after BOTHRESH(3) the message is moved to D5.ERROR - never discarded.")

# 3. Business acknowledgements -> central ledger (the edge only accepts an event as done once this is recorded)
flow("D5_AckDispatcher",
     [(1, "MQInput", "Business_Acks", (20, 120), {"queueName": "D5.OUT.ACK", "messageDomainProperty": "JSON", "transactionMode": "yes"}),
      (2, "Compute", "Prepare_Ack_Call", (220, 120), esql("D5_PrepareAckCall")),
      (3, "HTTPRequest", "Record_Ack_In_Ledger", (440, 120),
       {"URLSpecifier": CENTRAL + "/internal/ack", "httpMethod": "POST", "httpVersion": "1.1", "timeoutForServer": "15",
        "messageDomainProperty": "JSON"})],
     [(1, "out", 2), (2, "out", 3)],
     udps=CENTRAL_UDP,
     note="Consumes D5.OUT.ACK under syncpoint and records the acknowledgement in the central ledger. Error/failure terminals "
          "are deliberately unconnected: the get rolls back and is retried (BOTHRESH 10, then D5.ERROR).")

# 4. Edge status query -> central ledger
flow("D5_EventStatus",
     [(1, "HTTPInput", "Event_Status_Query", (20, 120), {"URLSpecifier": "/d5/v1/event-status", "messageDomainProperty": "BLOB"}),
      (2, "Compute", "Prepare_Status_Call", (220, 120), esql("D5_PrepareStatusCall")),
      (3, "HTTPRequest", "Query_Central_Ledger", (440, 120),
       {"URLSpecifier": CENTRAL + "/internal/event-status", "httpMethod": "GET", "httpVersion": "1.1", "timeoutForServer": "10",
        "messageDomainProperty": "BLOB"}),
      (4, "Compute", "Relay_Status", (680, 80), esql("D5_RelayStatus")),
      (5, "Compute", "Status_Unavailable", (680, 220), esql("D5_StatusUnavailable")),
      (6, "HTTPReply", "Reply", (900, 120), {})],
     [(1, "out", 2), (2, "out", 3), (3, "out", 4), (3, "error", 4), (3, "failure", 5), (4, "out", 6), (5, "out", 6)],
     udps=CENTRAL_UDP,
     note="GET /d5/v1/event-status?eventId=<uuid>. Returns the central ledger state (PROCESSED/REJECTED/UNKNOWN and whether the "
          "business ack from D5.OUT.ACK has been recorded).")
