vcl 4.1;

# Installed-package smoke VCL. It exercises exactly the surface the packaging
# plan's 11-step scenario requires: importing the packaged VMOD, registering
# tags from a backend response header, purging by tag, and rejecting an object
# whose tag has been purged.

import cachetag;

backend default {
	.host = "127.0.0.1";
	.port = "8080";
}

sub vcl_init {
	new tags = cachetag.namespace("default");
}

sub vcl_recv {
	if (req.method == "PURGE") {
		if (req.http.Cache-Tag-Purge) {
			set req.http.purged =
			    tags.purge_header(req.http.Cache-Tag-Purge);
			return (synth(200, "purged"));
		}
		return (synth(400, "no tag given"));
	}
}

sub vcl_backend_response {
	if (beresp.http.Cache-Tag) {
		tags.add_header(beresp.http.Cache-Tag);
		unset beresp.http.Cache-Tag;
	}
}

sub vcl_hit {
	if (tags.stale()) {
		return (restart);
	}
}

sub vcl_deliver {
	if (tags.stale()) {
		return (restart);
	}
	if (obj.hits > 0) {
		set resp.http.X-Cache = "HIT";
	} else {
		set resp.http.X-Cache = "MISS";
	}
	set resp.http.X-Tag-Objects = tags.objects();
}

sub vcl_synth {
	if (req.http.purged) {
		set resp.http.X-Purged = req.http.purged;
	}
	return (deliver);
}
