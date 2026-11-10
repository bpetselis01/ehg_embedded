{
	"subject": {{ toJson .Subject }},
	"keyUsage": ["digitalSignature"],
	"extKeyUsage": ["clientAuth"],
	"crlDistributionPoints": ["https://step-ca:9000/1.0/crl"]
}
